#como rodar? 
    # Rodar um único cenário (uma equipe)
    # K=5, 15s, tempo de corrida por perna ~ 10..30 ms, usando threading.Barrier
        # python ex09.py --k 5 --duration 15 --run-ms 10,30 --impl barrier

    # Usar a barreira implementada com mutex+condvar
        # python ex09.py --k 5 --duration 15 --run-ms 10,30 --impl cond

    # Medir rodadas por minuto para vários tamanhos de equipe (CSV)
    # testa K = 2,3,4,5,8 por 20s cada
        # python ex09.py --sweep 2,3,4,5,8 --duration 20 --run-ms 8,20 --impl barrier

# -*- coding: utf-8 -*-
import argparse, threading as th, time, random, statistics
from typing import Tuple, List, Optional

# ---------- Barreira condicional (mutex + condvar), alternativa à threading.Barrier ----------
class BrokenBarrierError(Exception): pass

class CondBarrier:
    def __init__(self, parties: int, action=None):
        self.parties = parties
        self.count = 0
        self.generation = 0
        self.lock = th.Lock()
        self.cond = th.Condition(self.lock)
        self.broken = False
        self.action = action

    def wait(self, timeout: Optional[float] = None) -> int:
        with self.lock:
            if self.broken:
                raise BrokenBarrierError()
            gen = self.generation
            self.count += 1
            if self.count == self.parties:
                # última thread a chegar: executa ação, avança geração e libera geral
                if self.action:
                    self.action()
                self.generation += 1
                self.count = 0
                self.cond.notify_all()
                return 0
            # aguarda as demais
            end = None if timeout is None else (time.perf_counter() + timeout)
            while gen == self.generation and not self.broken:
                remaining = None if end is None else (end - time.perf_counter())
                if remaining is not None and remaining <= 0:
                    # timeout quebra barreira para evitar deadlock
                    self.broken = True
                    self.cond.notify_all()
                    raise BrokenBarrierError()
                self.cond.wait(remaining)
            if self.broken:
                raise BrokenBarrierError()
            return 1

    def abort(self):
        with self.lock:
            self.broken = True
            self.cond.notify_all()

# ---------- Métricas por corredor ----------
class RunnerStats:
    def __init__(self):
        self.legs = 0
        self.max_wait_s = 0.0

# ---------- Worker: cada thread representa um corredor ----------
def runner(pid: int,
           barrier,
           stop_evt: th.Event,
           run_ms_range: Tuple[int,int],
           per_runner: List[RunnerStats]):
    rnd = random.Random(0xC0FFEE ^ pid)
    stats = per_runner[pid]
    while not stop_evt.is_set():
        # Corre a sua parte da perna
        ms = rnd.randint(run_ms_range[0], run_ms_range[1])
        time.sleep(ms/1000.0)
        # Chega na barreira e mede espera
        t0 = time.perf_counter()
        try:
            # pequeno timeout para garantir que a thread não fique eternamente presa ao encerrar
            barrier.wait(timeout=2.0)
        except Exception:
            # barreira abortada ou quebrada → encerrar limpo
            break
        waited = time.perf_counter() - t0
        stats.legs += 1
        if waited > stats.max_wait_s:
            stats.max_wait_s = waited

# ---------- Execução de um experimento com 1 equipe ----------
def run_experiment(team_size: int,
                   duration_s: int,
                   run_ms: Tuple[int,int],
                   impl: str = "barrier"):
    rounds = 0
    rounds_lock = th.Lock()

    def on_round_complete():
        nonlocal rounds
        with rounds_lock:
            rounds += 1

    # Escolha de barreira
    if impl == "cond":
        barrier = CondBarrier(team_size, action=on_round_complete)
    else:
        barrier = th.Barrier(team_size, action=on_round_complete)

    stop_evt = th.Event()
    per_runner = [RunnerStats() for _ in range(team_size)]
    threads = [th.Thread(target=runner, args=(i, barrier, stop_evt, run_ms, per_runner), daemon=False)
               for i in range(team_size)]

    t0 = time.perf_counter()
    for t in threads: t.start()

    # roda por duração solicitada
    time.sleep(duration_s)
    stop_evt.set()

    # aborta a barreira para liberar quem estiver esperando
    try:
        if hasattr(barrier, "abort"):
            barrier.abort()
        else:
            barrier.reset()  # threading.Barrier: reset quebra a barreira
    except Exception:
        pass

    for t in threads:
        t.join(timeout=3.0)
    elapsed = time.perf_counter() - t0

    rpm = (rounds / elapsed) * 60.0 if elapsed > 0 else 0.0
    waits = [r.max_wait_s for r in per_runner]
    legs = [r.legs for r in per_runner]

    return {
        "team_size": team_size,
        "duration_s": elapsed,
        "rounds": rounds,
        "rpm": rpm,
        "legs_per_runner": legs,
        "max_wait_ms_per_runner": [w*1000.0 for w in waits],
        "legs_mean": statistics.mean(legs) if legs else 0.0,
        "legs_stdev": statistics.pstdev(legs) if len(legs) > 1 else 0.0,
        "max_wait_ms_mean": statistics.mean([w*1000.0 for w in waits]) if waits else 0.0,
        "max_wait_ms_p95": (sorted([w*1000.0 for w in waits])[int(0.95*(len(waits)-1))] if waits else 0.0),
    }

# ---------- CLI ----------
def main():
    ap = argparse.ArgumentParser(description="Corrida de revezamento com barreira (threads)")
    ap.add_argument("--k", type=int, default=5, help="Tamanho da equipe (número de threads)")
    ap.add_argument("--duration", type=int, default=15, help="Duração do teste (s)")
    ap.add_argument("--run-ms", type=str, default="10,30", help="Tempo de corrida por perna (ms), ex: 10,30")
    ap.add_argument("--impl", choices=["barrier","cond"], default="barrier",
                    help="Implementação da barreira: 'barrier' (threading.Barrier) ou 'cond' (mutex+condvar)")
    ap.add_argument("--sweep", type=str, default="", help="Lista de K para varrer, ex: 2,3,4,5,8 (imprime CSV)")
    args = ap.parse_args()

    rlo, rhi = [int(x) for x in args.run_ms.split(",")]
    if rhi < rlo: rhi = rlo
    if args.k < 2: args.k = 2

    if args.sweep:
        ks = [int(x) for x in args.sweep.split(",") if x.strip()]
        print("k,duration_s,rounds,rpm,legs_mean,legs_stdev,max_wait_ms_mean,max_wait_ms_p95")
        for k in ks:
            res = run_experiment(k, args.duration, (rlo, rhi), impl=args.impl)
            print(f"{res['team_size']},{res['duration_s']:.3f},{res['rounds']},{res['rpm']:.2f},"
                  f"{res['legs_mean']:.2f},{res['legs_stdev']:.2f},{res['max_wait_ms_mean']:.2f},{res['max_wait_ms_p95']:.2f}")
    else:
        res = run_experiment(args.k, args.duration, (rlo, rhi), impl=args.impl)
        print("\n=== RESULTADOS ===")
        print(f"Equipe K={res['team_size']} | Duração={res['duration_s']:.2f}s")
        print(f"Rodadas concluídas: {res['rounds']}  | RPM: {res['rpm']:.2f}")
        print(f"Refeições… opa 😅  Pernas por corredor: {res['legs_per_runner']}")
        print(f"Desvio entre corredores (legs stdev): {res['legs_stdev']:.2f}")
        print(f"Espera máxima na barreira por corredor (ms): "
              f"{[round(x,1) for x in res['max_wait_ms_per_runner']]}")
        print(f"Espera média (ms): {res['max_wait_ms_mean']:.2f} | p95: {res['max_wait_ms_p95']:.2f}")

if __name__ == "__main__":
    main()
