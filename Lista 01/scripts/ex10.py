#como rodar? 
# Forçar deadlock e ver o relatório do watchdog
#   python ex10.py --mode deadlock --resources 4 --threads 4 --iters 100 --hold-ms 10,30 --wd-timeout 2.0
#
# Corrigir com ordem total de travamento (sem deadlock)
#   python ex10.py --mode ordered --resources 4 --threads 4 --iters 100 --hold-ms 10,30

# -*- coding: utf-8 -*-
import argparse, threading as th, time
from typing import Optional, Dict, Set, List, Tuple

# ---------- Registro global para diagnóstico ----------
_registry_lock = th.Lock()
_threads_info: Dict[int, Dict] = {}   # tid -> {name, holds:set(res), waiting:res|None, last_progress:float}
_resources_info: Dict[str, Dict] = {} # res_name -> {owner_tid:int|None, waiters:Set[int]}

def _now() -> float:
    return time.perf_counter()

def register_thread(name: str):
    tid = th.get_ident()
    with _registry_lock:
        _threads_info[tid] = dict(name=name, holds=set(), waiting=None, last_progress=_now())

def touch_progress():
    tid = th.get_ident()
    with _registry_lock:
        if tid in _threads_info:
            _threads_info[tid]["last_progress"] = _now()

def register_resource(res_name: str):
    with _registry_lock:
        _resources_info[res_name] = dict(owner_tid=None, waiters=set())

# ---------- Lock monitorado ----------
class MonitoredLock:
    def __init__(self, name: str):
        self.name = name
        self._lock = th.Lock()
        register_resource(name)

    def acquire(self, blocking: bool = True, timeout: Optional[float] = -1) -> bool:
        tid = th.get_ident()
        with _registry_lock:
            if tid in _threads_info:
                _threads_info[tid]["waiting"] = self.name
                _resources_info[self.name]["waiters"].add(tid)
        ok = self._lock.acquire(blocking, timeout) if timeout is not None else self._lock.acquire(blocking)
        with _registry_lock:
            if tid in _threads_info:
                _threads_info[tid]["waiting"] = None
                if ok:
                    _threads_info[tid]["holds"].add(self.name)
                    _resources_info[self.name]["owner_tid"] = tid
            _resources_info[self.name]["waiters"].discard(tid)
        return ok

    def release(self):
        tid = th.get_ident()
        with _registry_lock:
            if tid in _threads_info:
                _threads_info[tid]["holds"].discard(self.name)
            _resources_info[self.name]["owner_tid"] = None
        self._lock.release()

# ---------- Relatório do watchdog ----------
def build_wait_for_graph() -> Dict[int, Set[int]]:
    g: Dict[int, Set[int]] = {}
    with _registry_lock:
        for tid, info in _threads_info.items():
            w = info.get("waiting")
            if w:
                owner = _resources_info.get(w, {}).get("owner_tid")
                if owner is not None and owner != tid:
                    g.setdefault(tid, set()).add(owner)
    return g

def find_cycles(g: Dict[int, Set[int]]) -> List[List[int]]:
    cycles = []
    color: Dict[int, int] = {}
    stack: List[int] = []
    def dfs(u: int):
        color[u] = 1; stack.append(u)
        for v in g.get(u, ()):
            if color.get(v, 0) == 0:
                dfs(v)
            elif color.get(v, 0) == 1:
                if v in stack:
                    i = stack.index(v)
                    cycles.append(stack[i:] + [v])
        color[u] = 2; stack.pop()
    for u in list(g.keys()):
        if color.get(u, 0) == 0:
            dfs(u)
    return cycles

def _fmt_tid(tid: int) -> str:
    with _registry_lock:
        name = _threads_info.get(tid, {}).get("name", str(tid))
    return f"{name}({tid})"

def watchdog_loop(stop_evt: th.Event, fired_evt: th.Event,
                  timeout_s: float, check_s: float):
    register_thread("watchdog")
    last_progress = _now()
    while not stop_evt.is_set():
        time.sleep(check_s)
        with _registry_lock:
            lp_max = max((info["last_progress"] for info in _threads_info.values()), default=last_progress)
        if lp_max > last_progress:
            last_progress = lp_max
            continue
        if (_now() - last_progress) >= timeout_s:
            fired_evt.set()
            print("\n[WATCHDOG] Ausência de progresso detectada!")
            with _registry_lock:
                print("  Recursos:")
                for rname, rinfo in _resources_info.items():
                    owner = rinfo["owner_tid"]
                    waiters = list(rinfo["waiters"])
                    owner_s = _fmt_tid(owner) if owner is not None else "None"
                    waiter_s = ", ".join(_fmt_tid(t) for t in waiters) if waiters else "-"
                    print(f"   - {rname}: owner={owner_s}; waiters=[{waiter_s}]")
                print("  Threads:")
                for tid, info in _threads_info.items():
                    holds = ",".join(sorted(info["holds"])) or "-"
                    waiting = info["waiting"] or "-"
                    print(f"   - {_fmt_tid(tid)} holds=[{holds}] waiting={waiting}")
            g = build_wait_for_graph()
            cycles = find_cycles(g)
            if cycles:
                print("  Ciclos no grafo T->T (indicativo de deadlock):")
                for cyc in cycles:
                    print("   * " + " -> ".join(_fmt_tid(t) for t in cyc))
            else:
                print("  Sem ciclos detectados (pode ser starvation/slowdown).")
            stop_evt.set()
            break

# ---------- Workers ----------
def work_pair(i: int, locks: List[MonitoredLock], mode: str, stop_evt: th.Event,
              iters_per_thread: int, hold_ms: Tuple[int,int]):
    register_thread(f"worker-{i}")
    import random
    rnd = random.Random(0xA11CE ^ i)
    n = len(locks)
    left = i % n
    right = (i+1) % n
    first, second = left, right
    if mode == "deadlock" and (i % 2 == 1):
        first, second = right, left
    if mode == "ordered":
        if second < first:
            first, second = second, first
    for _ in range(iters_per_thread):
        if stop_evt.is_set():
            break
        if not locks[first].acquire(True):
            break
        try:
            if not locks[second].acquire(True):
                break
            try:
                time.sleep(rnd.randint(*hold_ms)/1000.0)
                touch_progress()
            finally:
                locks[second].release()
        finally:
            locks[first].release()

# ---------- Execução ----------
def run(mode: str, resources: int, threads: int, iters: int,
        hold_ms: Tuple[int,int], wd_timeout: float, wd_check: float):
    locks = [MonitoredLock(f"L{i}") for i in range(resources)]
    stop_evt = th.Event()
    wd_fired = th.Event()

    # Watchdog só no modo DEADLOCK
    start_wd = (mode == "deadlock")
    if start_wd:
        wd = th.Thread(target=watchdog_loop, args=(stop_evt, wd_fired, wd_timeout, wd_check), daemon=True)
        wd.start()
    else:
        wd = None  # no-op

    # Workers
    ws = []
    t0 = _now()
    for i in range(threads):
        t = th.Thread(target=work_pair, args=(i, locks, mode, stop_evt, iters, hold_ms), daemon=False)
        ws.append(t)
        t.start()

    for t in ws:
        t.join()
    elapsed = _now() - t0

    # Avaliação (antes de parar watchdog, se houver)
    all_done = not any(t.is_alive() for t in ws)
    deadlock_detected = wd_fired.is_set()
    completed = all_done and not deadlock_detected

    if start_wd:
        stop_evt.set()
        wd.join(timeout=1.0)

    total_attempted = threads * iters
    status = "OK (sem deadlock)" if (mode == "ordered" and completed) else (
             "Deadlock detectado" if deadlock_detected else "OK (?)")
    rpm = (total_attempted / elapsed) * 60.0 if elapsed > 0 and completed else 0.0

    print("\n=== RESUMO ===")
    print(f"Modo: {mode} | recursos={resources} | threads={threads} | iters/thread={iters}")
    print(f"Tempo: {elapsed:.2f}s | Status: {status}")
    if completed:
        print(f"Progresso: {total_attempted} seções críticas concluídas | ~{rpm:.1f} / min")
    elif deadlock_detected:
        print("Progresso: interrompido pelo watchdog (sem avanço).")
    else:
        print("Progresso: finalizado antes do previsto (sem watchdog).")

def parse_range(s: str) -> Tuple[int,int]:
    a,b = s.split(",")
    x,y = int(a), int(b)
    if y < x: y = x
    return (x,y)

def main():
    ap = argparse.ArgumentParser(description="Deadlock proposital + watchdog e correção com ordem total (threads, Python)")
    ap.add_argument("--mode", choices=["deadlock","ordered"], default="deadlock",
                    help="deadlock = ordens opostas; ordered = ordem global por índice")
    ap.add_argument("--resources", type=int, default=4, help="Quantidade de recursos (locks)")
    ap.add_argument("--threads", type=int, default=4, help="Quantidade de threads de trabalho")
    ap.add_argument("--iters", type=int, default=100, help="Iterações por thread (ordered). Em deadlock, tende a parar cedo.")
    ap.add_argument("--hold-ms", type=str, default="10,30", help="Tempo segurando os 2 locks (min,max) ms")
    ap.add_argument("--wd-timeout", type=float, default=2.0, help="Timeout do watchdog (s) sem progresso")
    ap.add_argument("--wd-check", type=float, default=0.2, help="Intervalo de checagem do watchdog (s)")
    args = ap.parse_args()

    if args.resources < 2: args.resources = 2
    if args.threads   < 2: args.threads   = 2
    hold_ms = parse_range(args.hold_ms)

    print(f"[INFO] Iniciando modo={args.mode} | R={args.resources} | T={args.threads} | iters={args.iters} | hold={hold_ms} ms")
    run(args.mode, args.resources, args.threads, args.iters, hold_ms, args.wd_timeout, args.wd_check)

if __name__ == "__main__":
    main()
