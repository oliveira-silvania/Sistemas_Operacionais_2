#como rodar? 
    # Execução única
        # python ex02.py -b 8 -P 4 -C 4 -d 15 --pmin 1 --pmax 5 --cmin 1 --cmax 5

    # Experimento variando o buffer 
        # python ex02.py --sweep 1,2,4,8,16,32 -P 4 -C 4 -d 15 --pmin 1 --pmax 5 --cmin 1 --cmax 5

# -*- coding: utf-8 -*-
import argparse
import random
import threading as th
import time
from collections import deque
from typing import Deque, Optional, Tuple, List

NS = 1_000_000_000

def now_ns() -> int:
    return time.perf_counter_ns()

def sleep_ms(ms: float) -> None:
    if ms <= 0:
        return
    time.sleep(ms / 1000.0)

class BoundedCircularBuffer:
    """
    Buffer circular limitado, com:
      - Semáforo empty_slots: quantos espaços livres existem
      - Semáforo filled_slots: quantos itens disponíveis existem
      - Mutex para acessar/alterar o deque
    Medimos o tempo bloqueado em empty_slots / filled_slots como 'espera'.
    """
    def __init__(self, capacity: int):
        assert capacity > 0
        self.capacity = capacity
        self.q: Deque[int] = deque(maxlen=capacity)
        self.lock = th.Lock()
        self.empty_slots = th.Semaphore(capacity)
        self.filled_slots = th.Semaphore(0)
        self.running = True  # para indicar fim ordenado

    def put(self, item: int) -> Tuple[bool, int]:
        """
        Enfileira item. Retorna (ok, wait_ns).
        ok=False se running foi desativado enquanto aguardava (não usado aqui, mas deixado para extensões).
        """
        t0 = now_ns()
        self.empty_slots.acquire()  # bloqueia até haver espaço
        wait_ns = now_ns() - t0
        # região crítica
        with self.lock:
            if not self.running:
                # desaloca o espaço que pegamos
                self.empty_slots.release()
                return (False, wait_ns)
            self.q.append(item)
        self.filled_slots.release()
        return (True, wait_ns)

    def get(self) -> Tuple[Optional[int], int, bool]:
        """
        Retira item. Retorna (item|None, wait_ns, ok)
        ok=False indica encerramento sem item.
        """
        t0 = now_ns()
        self.filled_slots.acquire()  # bloqueia até haver item
        wait_ns = now_ns() - t0
        with self.lock:
            if not self.running and len(self.q) == 0:
                # sinal para encerrar
                self.filled_slots.release()
                return (None, wait_ns, False)
            item = self.q.popleft()
        self.empty_slots.release()
        return (item, wait_ns, True)

    def stop(self):
        # sinaliza parada, mas não “desbloqueia” quem está esperando:
        # o driver é quem finaliza o escoamento.
        with self.lock:
            self.running = False

class Metrics:
    def __init__(self):
        self.mtx = th.Lock()
        # contadores
        self.produced = 0
        self.consumed = 0
        # esperas acumuladas (ns) e tentativas (para médias)
        self.p_wait_total_ns = 0
        self.c_wait_total_ns = 0
        self.p_attempts = 0
        self.c_attempts = 0

    def add_prod(self, wait_ns: int):
        with self.mtx:
            self.produced += 1
            self.p_attempts += 1
            self.p_wait_total_ns += wait_ns

    def add_cons(self, wait_ns: int):
        with self.mtx:
            self.consumed += 1
            self.c_attempts += 1
            self.c_wait_total_ns += wait_ns

    def snapshot(self):
        with self.mtx:
            return (self.produced, self.consumed,
                    self.p_wait_total_ns, self.c_wait_total_ns,
                    self.p_attempts, self.c_attempts)

def producer_loop(buf: BoundedCircularBuffer,
                  metrics: Metrics,
                  sleep_min_ms: int, sleep_max_ms: int,
                  rng_seed: int):
    rnd = random.Random(rng_seed)
    while buf.running:
        # simula tempo para produzir
        sleep_ms(rnd.randint(sleep_min_ms, sleep_max_ms))
        # gera item
        item = rnd.randrange(1, 1_000_000_000)
        ok, wait_ns = buf.put(item)
        if not ok:
            break
        metrics.add_prod(wait_ns)

def consumer_loop(buf: BoundedCircularBuffer,
                  metrics: Metrics,
                  sleep_min_ms: int, sleep_max_ms: int,
                  rng_seed: int):
    rnd = random.Random(rng_seed)
    while True:
        item, wait_ns, ok = buf.get()
        if not ok:
            break
        # simula tempo para consumir
        sleep_ms(rnd.randint(sleep_min_ms, sleep_max_ms))
        metrics.add_cons(wait_ns)

def run_once(buffer_cap: int, P: int, C: int, duration_s: int,
             pmin: int, pmax: int, cmin: int, cmax: int) -> dict:
    """
    Executa um experimento e retorna métricas agregadas.
    pmin/pmax/cmin/cmax em milissegundos.
    """
    buf = BoundedCircularBuffer(buffer_cap)
    metrics = Metrics()

    producers: List[th.Thread] = []
    consumers: List[th.Thread] = []

    start_ns = now_ns()

    # consumidores primeiro (para evitar ‘arranque a frio’)
    for i in range(C):
        t = th.Thread(target=consumer_loop,
                      args=(buf, metrics, cmin, cmax, (i+1)*1337),
                      daemon=False)
        consumers.append(t)
        t.start()

    for i in range(P):
        t = th.Thread(target=producer_loop,
                      args=(buf, metrics, pmin, pmax, (i+1)*911),
                      daemon=False)
        producers.append(t)
        t.start()

    # roda por duration_s
    time.sleep(duration_s)

    # parar produção e drenar o buffer
    buf.stop()

    # aguarda produtores
    for t in producers:
        t.join(timeout=1.0)

    # Agora precisamos “destravar” consumidores que podem estar presos no filled_slots
    # Estratégia: enquanto houver itens, os consumidores seguirão fluindo; quando esvaziar,
    # liberamos C vezes o filled_slots e marcamos ok=False.
    # Como nosso get() retorna ok=False apenas se running=False E fila vazia,
    # fazemos um ciclo curto para garantir o desbloqueio final.
    # (Simplificação: acordar consumidores extras após producers finalizarem)
    for _ in range(len(consumers)):
        buf.filled_slots.release()

    for t in consumers:
        t.join(timeout=1.0)

    end_ns = now_ns()

    produced, consumed, p_wait_ns, c_wait_ns, p_att, c_att = metrics.snapshot()
    elapsed_s = (end_ns - start_ns) / NS
    throughput = consumed / elapsed_s if elapsed_s > 0 else 0.0
    avg_p_wait_ms = (p_wait_ns / p_att / 1e6) if p_att else 0.0
    avg_c_wait_ms = (c_wait_ns / c_att / 1e6) if c_att else 0.0

    return {
        "buffer": buffer_cap,
        "P": P,
        "C": C,
        "duration_s": duration_s,
        "pmin_ms": pmin,
        "pmax_ms": pmax,
        "cmin_ms": cmin,
        "cmax_ms": cmax,
        "produced": produced,
        "consumed": consumed,
        "elapsed_s": elapsed_s,
        "throughput": throughput,
        "avg_p_wait_ms": avg_p_wait_ms,
        "avg_c_wait_ms": avg_c_wait_ms,
    }

def print_summary(res: dict):
    print("\n=== RESULTADOS ===")
    print(f"Buffer: {res['buffer']} | Produtores: {res['P']} | Consumidores: {res['C']} | Duração: {res['duration_s']}s")
    print(f"Prod sleep (ms): [{res['pmin_ms']}, {res['pmax_ms']}], Cons sleep (ms): [{res['cmin_ms']}, {res['cmax_ms']}]")
    print(f"Itens produzidos:  {res['produced']}")
    print(f"Itens consumidos:  {res['consumed']}")
    print(f"Tempo total:       {res['elapsed_s']:.3f}s")
    print(f"Throughput:        {res['throughput']:.2f} itens/s")
    print(f"Espera média Prod: {res['avg_p_wait_ms']:.3f} ms/item")
    print(f"Espera média Cons: {res['avg_c_wait_ms']:.3f} ms/item")
    print("\nCSV,buffer,P,C,duration_s,pmin_ms,pmax_ms,cmin_ms,cmax_ms,produced,consumed,elapsed_s,throughput,avg_p_wait_ms,avg_c_wait_ms")
    print(f"CSV,{res['buffer']},{res['P']},{res['C']},{res['duration_s']},"
          f"{res['pmin_ms']},{res['pmax_ms']},{res['cmin_ms']},{res['cmax_ms']},"
          f"{res['produced']},{res['consumed']},{res['elapsed_s']:.6f},"
          f"{res['throughput']:.6f},{res['avg_p_wait_ms']:.6f},{res['avg_c_wait_ms']:.6f}")

def main():
    ap = argparse.ArgumentParser(description="Produtores/Consumidores com buffer circular (Python + threading)")
    ap.add_argument("-b", "--buffer", type=int, default=8, help="Tamanho do buffer")
    ap.add_argument("-P", "--producers", type=int, default=2, help="Número de produtores")
    ap.add_argument("-C", "--consumers", type=int, default=2, help="Número de consumidores")
    ap.add_argument("-d", "--duration", type=int, default=10, help="Duração do experimento (s)")
    ap.add_argument("--pmin", type=int, default=1, help="Sleep mínimo do produtor (ms)")
    ap.add_argument("--pmax", type=int, default=5, help="Sleep máximo do produtor (ms)")
    ap.add_argument("--cmin", type=int, default=1, help="Sleep mínimo do consumidor (ms)")
    ap.add_argument("--cmax", type=int, default=5, help="Sleep máximo do consumidor (ms)")
    ap.add_argument("--sweep", type=str, default="", help="Lista de tamanhos de buffer, ex: 1,2,4,8,16")
    args = ap.parse_args()

    # sanity
    if args.pmax < args.pmin: args.pmax = args.pmin
    if args.cmax < args.cmin: args.cmax = args.cmin
    if args.buffer <= 0: args.buffer = 1
    if args.producers <= 0: args.producers = 1
    if args.consumers <= 0: args.consumers = 1
    if args.duration <= 0: args.duration = 5

    if args.sweep.strip():
        sizes = [int(x) for x in args.sweep.split(",") if x.strip()]
        print("buffer,P,C,duration_s,pmin_ms,pmax_ms,cmin_ms,cmax_ms,produced,consumed,elapsed_s,throughput,avg_p_wait_ms,avg_c_wait_ms")
        for b in sizes:
            res = run_once(
                buffer_cap=max(1, b),
                P=args.producers, C=args.consumers,
                duration_s=args.duration,
                pmin=args.pmin, pmax=args.pmax,
                cmin=args.cmin, cmax=args.cmax
            )
            print(f"{res['buffer']},{res['P']},{res['C']},{res['duration_s']},"
                  f"{res['pmin_ms']},{res['pmax_ms']},{res['cmin_ms']},{res['cmax_ms']},"
                  f"{res['produced']},{res['consumed']},{res['elapsed_s']:.6f},"
                  f"{res['throughput']:.6f},{res['avg_p_wait_ms']:.6f},{res['avg_c_wait_ms']:.6f}")
    else:
        res = run_once(
            buffer_cap=args.buffer,
            P=args.producers, C=args.consumers,
            duration_s=args.duration,
            pmin=args.pmin, pmax=args.pmax,
            cmin=args.cmin, cmax=args.cmax
        )
        print_summary(res)

if __name__ == "__main__":
    main()
