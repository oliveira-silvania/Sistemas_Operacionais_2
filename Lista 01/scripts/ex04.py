#como rodar? 
        # python ex04.py -n 1000 -c1 8 -c2 8 --cap-ms 1,4 --proc-ms 2,5 --grav-ms 1,3

        # Varie capacidades pequenas para estressar backpressure:
            # python ex04.py -n 1000 -c1 1 -c2 1

# -*- coding: utf-8 -*-
import argparse
import threading as th
from collections import deque
import time, random

# ==========================
# Fila limitada: mutex + condição (espera ativa zero)
# ==========================
class BoundedQueue:
    def __init__(self, capacity: int):
        assert capacity > 0
        self.capacity = capacity
        self.q = deque()
        self.lock = th.Lock()
        self.not_empty = th.Condition(self.lock)
        self.not_full  = th.Condition(self.lock)

    def put(self, item):
        with self.not_full:
            while len(self.q) >= self.capacity:
                self.not_full.wait()
            self.q.append(item)
            self.not_empty.notify()

    def get(self):
        with self.not_empty:
            while len(self.q) == 0:
                self.not_empty.wait()
            item = self.q.popleft()
            self.not_full.notify()
            return item

# Sentinela (poison pill) para encerramento limpo
POISON = object()

# ==========================
# Threads de estágios
# ==========================
def captura(N, out_q: BoundedQueue, ids_capturados: set, delay_ms_min, delay_ms_max):
    rnd = random.Random(12345)
    for i in range(N):
        # simula tempo de captura
        time.sleep(rnd.uniform(delay_ms_min, delay_ms_max) / 1000.0)
        out_q.put(("frame", i))   # item = (tipo, id)
        ids_capturados.add(i)
    # poison pill para encerrar a cadeia
    out_q.put(POISON)

def processamento(in_q: BoundedQueue, out_q: BoundedQueue, ids_processados: set,
                  delay_ms_min, delay_ms_max):
    rnd = random.Random(67890)
    while True:
        item = in_q.get()
        if item is POISON:
            # encerrar e repassar poison pill adiante
            out_q.put(POISON)
            break
        kind, i = item
        # simula trabalho de CPU/IO do estágio
        time.sleep(rnd.uniform(delay_ms_min, delay_ms_max) / 1000.0)
        # "processa": aqui só marcamos o ID
        ids_processados.add(i)
        out_q.put(("proc", i))

def gravacao(in_q: BoundedQueue, ids_gravados: set, delay_ms_min, delay_ms_max):
    rnd = random.Random(54321)
    while True:
        item = in_q.get()
        if item is POISON:
            break
        kind, i = item
        # simula IO de gravação
        time.sleep(rnd.uniform(delay_ms_min, delay_ms_max) / 1000.0)
        ids_gravados.add(i)

# ==========================
# Driver + Asserções
# ==========================
def main():
    ap = argparse.ArgumentParser(description="Pipeline 3 estágios (captura -> processamento -> gravação) com threads")
    ap.add_argument("-n", "--num", type=int, default=1000, help="N itens a processar")
    ap.add_argument("-c1", "--cap1", type=int, default=8, help="Capacidade da fila entre captura e processamento")
    ap.add_argument("-c2", "--cap2", type=int, default=8, help="Capacidade da fila entre processamento e gravação")
    ap.add_argument("--cap-ms", type=str, default="1,4", help="Range ms captura: ex 1,4")
    ap.add_argument("--proc-ms", type=str, default="2,5", help="Range ms processamento: ex 2,5")
    ap.add_argument("--grav-ms", type=str, default="1,3", help="Range ms gravação: ex 1,3")
    args = ap.parse_args()

    N = max(1, args.num)
    cap1 = max(1, args.cap1)
    cap2 = max(1, args.cap2)

    cmin, cmax = [int(x) for x in args.cap_ms.split(",")]
    pmin, pmax = [int(x) for x in args.proc_ms.split(",")]
    gmin, gmax = [int(x) for x in args.grav_ms.split(",")]

    q12 = BoundedQueue(cap1)
    q23 = BoundedQueue(cap2)

    # Conjuntos para validar integridade (sem perdas/duplicações)
    ids_capturados = set()
    ids_processados = set()
    ids_gravados   = set()

    t_cap = th.Thread(target=captura, args=(N, q12, ids_capturados, cmin, cmax))
    t_proc = th.Thread(target=processamento, args=(q12, q23, ids_processados, pmin, pmax))
    t_grav = th.Thread(target=gravacao, args=(q23, ids_gravados, gmin, gmax))

    t0 = time.perf_counter()
    t_cap.start(); t_proc.start(); t_grav.start()
    # Aguarda todas as etapas (se houvesse deadlock, travaria aqui — o que não ocorre)
    t_cap.join(); t_proc.join(); t_grav.join()
    elapsed = time.perf_counter() - t0

    # ==========================
    # Provas via asserções
    # ==========================
    # 1) Todas as threads terminaram => não houve deadlock
    # (se chegamos aqui, join retornou nas 3)

    # 2) Integridade dos dados: IDs preservados sem perdas ou duplicações
    expected = set(range(N))
    assert ids_capturados == expected, f"Captura incorreta: {len(ids_capturados)} != {N}"
    assert ids_processados == expected, f"Processamento incorreto: {len(ids_processados)} != {N}"
    assert ids_gravados   == expected, f"Gravação incorreta: {len(ids_gravados)} != {N}"

    # 3) Cardinalidade igual em todos os estágios
    assert len(ids_capturados) == len(ids_processados) == len(ids_gravados) == N, "Tamanhos divergentes"

    # ==========================
    # Relatório
    # ==========================
    thr = N / elapsed if elapsed > 0 else 0.0
    print("\n=== PIPELINE OK ===")
    print(f"Itens: {N} | Fila1 cap: {cap1} | Fila2 cap: {cap2}")
    print(f"Tempo: {elapsed:.3f}s | Throughput: {thr:.1f} itens/s")
    print("Sem deadlock (todas as threads encerraram).")
    print("Sem perda/duplicação (IDs batem em todos os estágios).")

if __name__ == "__main__":
    main()
