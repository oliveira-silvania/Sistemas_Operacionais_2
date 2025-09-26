#como rodar? 
    # Cenário: bursts rápidos e consumidores lentos (estressa backpressure)
        # python ex08.py -b 32 -P 3 -C 2 -d 20 --burst-len 50 --burst-item-ms 0,2 --idle-ms 200,400 --consume-ms 5,12 --high 0.8 --low 0.5 --sample-ms 50 --csv ocupacao_lento.csv

    # Consumidores mais rápidos (buffer estabiliza com ocupação menor)
        # python ex08.py -b 32 -P 3 -C 4 -d 20 --burst-len 50 --burst-item-ms 0,2 --idle-ms 200,400 --consume-ms 1,4 --high 0.8 --low 0.5 --sample-ms 50 --csv ocupacao_rapido.csv

# -*- coding: utf-8 -*-
import argparse, threading as th, time, random, csv
from collections import deque
from typing import Deque, Tuple, Optional, List

def now_ns(): return time.perf_counter_ns()
def msleep(ms): 
    if ms > 0: time.sleep(ms/1000.0)
NS = 1_000_000_000

# ========================= Buffer limitado: mutex + Conditions =========================
class BoundedQueue:
    """
    Buffer circular limitado com:
      - not_full: bloqueia produtores quando cheio
      - not_empty: bloqueia consumidores quando vazio
      - backpressure: produtores aguardam quando ocupação >= high_mark e só seguem quando <= low_mark
    Espera ativa ZERO (wait/notify). put()/get() retornam tempo de espera (ns).
    """
    def __init__(self, capacity: int, high_pct: float, low_pct: float):
        assert capacity > 0
        self.cap = capacity
        self.q: Deque[int] = deque()
        self.lock = th.Lock()
        self.not_full  = th.Condition(self.lock)
        self.not_empty = th.Condition(self.lock)
        self.backpress = th.Condition(self.lock)

        # Histerese de backpressure (em itens)
        high = max(1, min(capacity, int(round(capacity * high_pct))))
        low  = max(0, min(high-1, int(round(capacity * low_pct))))
        if low >= high:  # garante histerese válida
            low = max(0, high-1)
        self.high_mark = high
        self.low_mark  = low

    def size(self) -> int:
        with self.lock:
            return len(self.q)

    def put(self, item) -> int:
        t0 = now_ns()
        with self.lock:
            # 1) backpressure (alto): aguarda cair para low_mark
            while len(self.q) >= self.high_mark:
                self.backpress.wait()
            # 2) capacidade (cheio): aguarda espaço
            while len(self.q) >= self.cap:
                self.not_full.wait()
            self.q.append(item)
            self.not_empty.notify()
        return now_ns() - t0

    def get(self) -> Tuple[object, int]:
        t0 = now_ns()
        with self.lock:
            while len(self.q) == 0:
                self.not_empty.wait()
            item = self.q.popleft()
            # sinaliza capacidade liberada
            self.not_full.notify()
            # se passamos de high->low, libere produtores em backpressure
            if len(self.q) <= self.low_mark:
                self.backpress.notify_all()
        return item, now_ns() - t0

# ========================= Sampler de ocupação =========================
class OccupancySampler(th.Thread):
    def __init__(self, buf: BoundedQueue, sample_ms: int):
        super().__init__(daemon=True)
        self.buf = buf
        self.sample_ms = sample_ms
        self.samples: List[Tuple[float,int]] = []
        self._start_t = time.perf_counter()
        self._stop = th.Event()
    def run(self):
        while not self._stop.is_set():
            t = time.perf_counter() - self._start_t
            self.samples.append((t, self.buf.size()))
            msleep(self.sample_ms)
        # captura última amostra
        t = time.perf_counter() - self._start_t
        self.samples.append((t, self.buf.size()))
    def stop(self):
        self._stop.set()

# ========================= Execução =========================
POISON = object()

def producer_loop(pid: int, buf: BoundedQueue, stop_evt: th.Event,
                  burst_len: int, burst_item_ms: Tuple[int,int], idle_ms: Tuple[int,int],
                  id_gen, metrics):
    rnd = random.Random(0xA11CE ^ pid)
    while not stop_evt.is_set():
        # Rajada de produção
        for _ in range(burst_len):
            if stop_evt.is_set():
                break
            item_id = id_gen()
            wns = buf.put(item_id)  # inclui tempo esperando por backpressure e/ou cheio
            with metrics['mtx']:
                metrics['produced'] += 1
                metrics['p_wait_ns'] += wns
            ms = rnd.randint(*burst_item_ms)
            msleep(ms)
        # Ociosidade pós-burst
        if stop_evt.is_set(): break
        ms = rnd.randint(*idle_ms)
        msleep(ms)

def consumer_loop(cid: int, buf: BoundedQueue, stop_evt: th.Event,
                  consume_ms: Tuple[int,int], metrics, consumed_ids: Optional[set]):
    rnd = random.Random(0xBEEF ^ cid)
    while True:
        item, wns = buf.get()    # aguarda se vazio
        if item is POISON:
            # repasse a poison para desbloquear demais consumidores, exceto este
            buf.put(POISON)
            break
        if consumed_ids is not None:
            consumed_ids.add(item)
        with metrics['mtx']:
            metrics['consumed'] += 1
            metrics['c_wait_ns'] += wns
        ms = rnd.randint(*consume_ms)
        msleep(ms)

def percentile(xs: List[float], p: float) -> float:
    if not xs: return 0.0
    xs = sorted(xs)
    k = max(0, min(len(xs)-1, int(round((p/100.0)*(len(xs)-1)))))
    return xs[k]

def main():
    ap = argparse.ArgumentParser(description="Produtor/Consumidor com bursts, backpressure e amostragem de ocupação (threads, Python)")
    ap.add_argument("-b","--buffer", type=int, default=32, help="Capacidade do buffer")
    ap.add_argument("-P","--producers", type=int, default=3, help="Número de produtores")
    ap.add_argument("-C","--consumers", type=int, default=2, help="Número de consumidores")
    ap.add_argument("-d","--duration", type=int, default=20, help="Duração (s) antes de encerrar produtores")
    ap.add_argument("--burst-len", type=int, default=50, help="Itens por rajada (burst)")
    ap.add_argument("--burst-item-ms", type=str, default="0,2", help="Sleep por item em burst (min,max) ms")
    ap.add_argument("--idle-ms", type=str, default="200,400", help="Sleep entre rajadas (min,max) ms")
    ap.add_argument("--consume-ms", type=str, default="5,12", help="Tempo de consumo por item (min,max) ms")
    ap.add_argument("--high", type=float, default=0.8, help="Limiar ALTO de backpressure (fração da capacidade)")
    ap.add_argument("--low", type=float, default=0.5, help="Limiar BAIXO (histerese; fração da capacidade)")
    ap.add_argument("--sample-ms", type=int, default=50, help="Período de amostragem da ocupação (ms)")
    ap.add_argument("--csv", type=str, default="", help="Salvar CSV com (t,ocupacao)")
    ap.add_argument("--check-ids", action="store_true", help="Verificar integridade por conjunto de IDs (custo de memória)")
    args = ap.parse_args()

    # Parse de ranges
    bi_lo, bi_hi = [int(x) for x in args.burst_item_ms.split(",")]
    id_lo, id_hi = [int(x) for x in args.idle_ms.split(",")]
    co_lo, co_hi = [int(x) for x in args.consume_ms.split(",")]
    if args.buffer <= 0: args.buffer = 1
    if args.producers <= 0: args.producers = 1
    if args.consumers <= 0: args.consumers = 1
    args.high = max(0.0, min(1.0, args.high))
    args.low  = max(0.0, min(1.0, args.low))

    buf = BoundedQueue(args.buffer, args.high, args.low)
    stop_evt = th.Event()

    # ID único por item
    id_lock = th.Lock()
    next_id = 0
    def id_gen():
        nonlocal next_id
        with id_lock:
            v = next_id
            next_id += 1
            return v

    # Métricas
    metrics = {'mtx': th.Lock(), 'produced': 0, 'consumed': 0, 'p_wait_ns': 0, 'c_wait_ns': 0}
    consumed_ids = set() if args.check_ids else None

    # Sampler
    sampler = OccupancySampler(buf, args.sample_ms); sampler.start()

    # Threads
    producers = [th.Thread(target=producer_loop, args=(i, buf, stop_evt,
                                                       args.burst_len, (bi_lo, bi_hi), (id_lo, id_hi),
                                                       id_gen, metrics), daemon=False)
                 for i in range(args.producers)]
    consumers = [th.Thread(target=consumer_loop, args=(i, buf, stop_evt,
                                                       (co_lo, co_hi), metrics, consumed_ids), daemon=False)
                 for i in range(args.consumers)]
    for t in consumers: t.start()
    for t in producers: t.start()

    # Roda por duração
    t0 = time.perf_counter()
    time.sleep(args.duration)
    stop_evt.set()
    # Espera produtores terminarem o ciclo atual
    for t in producers: t.join()
    # Injeta poison pills para encerrar consumidores
    buf.put(POISON)
    for t in consumers: t.join()
    elapsed = time.perf_counter() - t0

    # Para sampler
    sampler.stop(); sampler.join()

    # ===================== Relatório =====================
    produced = metrics['produced']; consumed = metrics['consumed']
    p_wait_ms = (metrics['p_wait_ns'] / max(1, produced)) / 1e6
    c_wait_ms = (metrics['c_wait_ns'] / max(1, consumed)) / 1e6
    thput = consumed / elapsed if elapsed > 0 else 0.0

    # Ocupação
    occ = [o for _,o in sampler.samples]
    occ_mean = sum(occ)/len(occ) if occ else 0.0
    occ_p95  = percentile([float(x) for x in occ], 95)
    occ_max  = max(occ) if occ else 0

    # Provas de estabilidade / integridade
    assert produced >= consumed, "Consumiu mais do que produziu (inconsistência)"
    assert buf.size() == 0 or occ[-1] == buf.size(), "Amostragem final inconsistente"
    if args.check_ids:
        # Checagem forte (sem perda/duplicação)
        assert len(consumed_ids) == consumed, "Itens duplicados detectados no consumo"
        assert len(consumed_ids) <= produced, "Mais IDs no consumo do que produzidos"

    print("\n=== RESULTADOS ===")
    print(f"Buffer: {args.buffer} | Produtores: {args.producers} | Consumidores: {args.consumers} | Duração: {args.duration}s")
    print(f"Backpressure: high={args.high:.2f} ({buf.high_mark}/{args.buffer}) | low={args.low:.2f} ({buf.low_mark}/{args.buffer})")
    print(f"Throughput:  {thput:,.1f} itens/s")
    print(f"Produzidos:  {produced} | Consumidos: {consumed} | Em buffer final: {buf.size()}")
    print(f"Espera Prod: {p_wait_ms:.3f} ms/item ( média de bloqueio por put )")
    print(f"Espera Cons: {c_wait_ms:.3f} ms/item ( média aguardando item )")
    print(f"Ocupação: média={occ_mean:.2f} | p95={occ_p95:.2f} | máx={occ_max}")

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t_s","ocupacao"])
            w.writerows(sampler.samples)
        print(f"CSV salvo em: {args.csv}")

if __name__ == "__main__":
    main()
