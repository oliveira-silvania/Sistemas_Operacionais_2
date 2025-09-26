# como rodar
""" Esse programa foi testado no cmd, pelo powershell não deu certo
Cria um arquivo de entrada (in.txt) na mesma pasta onde você está:
rode o seguinte comando no cmd:
    (
    echo prime 1000003
    echo fib 40
    echo prime 17
    ) > in.txt

Depois disso, digite: type in.txt

Vai aparecer:
    prime 1000003
    fib 40
    prime 17
 Executa o programa ex05.py lendo as tarefas desse arquivo:
    py ex05.py -w 4 --quiet < in.txt
"""
# -*- coding: utf-8 -*-
import sys, time, argparse, threading as th
from collections import deque
from dataclasses import dataclass
from typing import Optional, Tuple, Deque, List, Set

# ===================== Tarefas CPU-bound =====================

def is_prime(n: int) -> bool:
    if n < 2: 
        return False
    if n % 2 == 0:
        return n == 2
    r = int(n ** 0.5)
    i = 3
    while i <= r:
        if n % i == 0:
            return False
        i += 2
    return True

def fib_iter(n: int) -> int:
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a

# ===================== Fila concorrente (mutex + condition) =====================

class ConcurrentQueue:
    """Fila concorrente não-limitada com espera bloqueante (sem busy-wait)."""
    def __init__(self) -> None:
        self._q: Deque[object] = deque()
        self._lock = th.Lock()
        self._not_empty = th.Condition(self._lock)
        self._closed = False

    def put(self, item: object) -> None:
        with self._not_empty:
            if self._closed:
                raise RuntimeError("Queue closed")
            self._q.append(item)
            self._not_empty.notify()

    def get(self) -> Tuple[bool, Optional[object]]:
        with self._not_empty:
            while not self._q and not self._closed:
                self._not_empty.wait()
            if self._q:
                return True, self._q.popleft()
            # fechada e vazia
            return False, None

    def close(self) -> None:
        with self._not_empty:
            self._closed = True
            self._not_empty.notify_all()

# ===================== Descrição da tarefa =====================

@dataclass(frozen=True)
class Task:
    tid: int            # id único da tarefa
    kind: str           # "prime" | "fib"
    n: int              # parâmetro

# ===================== Thread pool =====================

class ThreadPool:
    def __init__(self, num_workers: int, quiet: bool = False) -> None:
        self.num_workers = max(1, num_workers)
        self.queue = ConcurrentQueue()
        self.workers: List[th.Thread] = []
        self.started = False
        self.quiet = quiet

        # Métricas / prova de integridade
        self._mtx = th.Lock()
        self.enqueued_ids: Set[int] = set()
        self.processed_ids: Set[int] = set()
        self.dup_enqueues = 0
        self.dup_process = 0
        self.tasks_done = 0

    def start(self) -> None:
        if self.started: 
            return
        self.started = True
        for wid in range(self.num_workers):
            t = th.Thread(target=self._worker, args=(wid,), daemon=False)
            self.workers.append(t)
            t.start()

    def submit(self, task: Task) -> None:
        with self._mtx:
            if task.tid in self.enqueued_ids:
                self.dup_enqueues += 1
            self.enqueued_ids.add(task.tid)
        self.queue.put(task)

    def _worker(self, wid: int) -> None:
        while True:
            ok, item = self.queue.get()
            if not ok:
                break
            task: Task = item  # type: ignore
            # Processa
            if task.kind == "prime":
                res = is_prime(task.n)
                if not self.quiet:
                    print(f"[W{wid}] prime({task.n}) -> {res}")
            elif task.kind == "fib":
                res = fib_iter(task.n)
                if not self.quiet:
                    print(f"[W{wid}] fib({task.n}) -> {res}")
            else:
                if not self.quiet:
                    print(f"[W{wid}] tarefa inválida: {task}")
            # Marca concluída
            with self._mtx:
                if task.tid in self.processed_ids:
                    self.dup_process += 1
                self.processed_ids.add(task.tid)
                self.tasks_done += 1

    def close_and_join(self) -> None:
        self.queue.close()
        for t in self.workers:
            t.join()

# ===================== Parsing de entrada =====================

def parse_line(line: str, next_tid: int) -> Optional[Task]:
    s = line.strip()
    if not s:
        return None
    parts = s.split()
    # Formatos aceitos:
    #   "prime <n>"
    #   "fib <n>"
    #   "<n>"   -> atalho: prime <n>
    if len(parts) == 1:
        kind = "prime"
        try:
            n = int(parts[0])
        except ValueError:
            return None
    else:
        kind = parts[0].lower()
        try:
            n = int(parts[1])
        except (IndexError, ValueError):
            return None
        if kind not in ("prime", "fib"):
            return None
    return Task(tid=next_tid, kind=kind, n=n)

# ===================== Main =====================

def main():
    ap = argparse.ArgumentParser(
        description="Pool fixo de threads (fila concorrente) para tarefas CPU-bound. "
                    "Lê de stdin até EOF e prova ausência de perdas/duplicações."
    )
    ap.add_argument("-w", "--workers", type=int, default=4, help="Número de threads do pool (>=1)")
    ap.add_argument("--quiet", action="store_true", help="Não imprimir resultados por tarefa (mostra só o resumo)")
    args = ap.parse_args()

    pool = ThreadPool(args.workers, quiet=args.quiet)
    pool.start()

    t0 = time.perf_counter()
    tid = 0

    try:
        # Enfileira até EOF
        for line in sys.stdin:
            task = parse_line(line, tid)
            if task is None:
                continue
            pool.submit(task)
            tid += 1
    except KeyboardInterrupt:
        # Se interrompido, ainda fechamos e juntamos o pool
        pass
    finally:
        # Sinaliza fim e aguarda
        pool.close_and_join()

    elapsed = time.perf_counter() - t0

    # ===================== Provas/asserções =====================
    # 1) Nenhuma tarefa perdida: enfileiradas == processadas e conjuntos idênticos
    enq = pool.enqueued_ids
    proc = pool.processed_ids
    assert len(enq) == len(proc), f"Tarefas perdidas: enq={len(enq)} != proc={len(proc)}"
    assert enq == proc, "Conjuntos de IDs enfileirados e processados divergentes"
    # 2) Sem duplicações (enfileirar/consumir) — se houver, acusa
    assert pool.dup_enqueues == 0, f"Tarefas enfileiradas duplicadas detectadas: {pool.dup_enqueues}"
    assert pool.dup_process == 0, f"Tarefas processadas duplicadas detectadas: {pool.dup_process}"

    # ===================== Resumo =====================
    thput = len(proc) / elapsed if elapsed > 0 else 0.0
    print("\n=== RESUMO ===")
    print(f"Workers:        {args.workers}")
    print(f"Tarefas lidas:  {len(enq)}")
    print(f"Tarefas feitas: {pool.tasks_done}")
    print(f"Tempo total:    {elapsed:.3f}s")
    print(f"Throughput:     {thput:,.1f} tarefas/s")
    print("Provas: nenhuma tarefa perdida; nenhuma duplicada; fila thread-safe (mutex + condition).")

if __name__ == "__main__":
    main()
