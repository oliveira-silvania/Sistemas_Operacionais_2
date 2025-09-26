#como rodar? 
    # Modo (a): ordem global (deadlock-free) + cortesia (anti-starvation)
        # python ex07.py --mode order --n 5 --duration 10 --think 20,60 --eat 15,40

    # Modo (b): semáforo FIFO limitando 4 simultâneos (para N=5)
        # python ex07.py --mode sem --n 5 --limit 4 --duration 10 --think 20,60 --eat 15,40

# -*- coding: utf-8 -*-
import argparse, random, threading as th, time
from collections import deque
from dataclasses import dataclass
from typing import List, Tuple, Optional

# ---------------------------- Util --------------------------------
def msleep(ms: float): 
    if ms > 0: time.sleep(ms/1000.0)

def now() -> float:
    return time.perf_counter()

@dataclass
class Metrics:
    meals: int = 0
    max_wait_s: float = 0.0
    sum_wait_s: float = 0.0

# -------------------------- Garçom FIFO ----------------------------
class FairWaiter:
    """Semáforo FIFO com capacidade limitada. Mitiga starvation via fila justa."""
    def __init__(self, capacity: int):
        self.cap = capacity
        self.lock = th.Lock()
        self.cv = th.Condition(self.lock)
        self.in_use = 0
        self.queue = deque()
        self.closing = False

    def acquire(self, pid: int, stop_event: th.Event) -> bool:
        with self.cv:
            self.queue.append(pid)
            while True:
                if self.closing or stop_event.is_set():
                    # retirar da fila e abortar
                    try:
                        self.queue.remove(pid)
                    except ValueError:
                        pass
                    return False
                is_head = (self.queue and self.queue[0] == pid)
                if is_head and self.in_use < self.cap:
                    self.in_use += 1
                    self.queue.popleft()
                    return True
                # espera justa
                self.cv.wait(timeout=0.1)

    def release(self):
        with self.cv:
            self.in_use -= 1
            if self.in_use < 0: self.in_use = 0
            self.cv.notify_all()

    def shutdown(self):
        with self.cv:
            self.closing = True
            self.cv.notify_all()

# ------------------------ Filósofo Thread --------------------------
class Philosopher(th.Thread):
    def __init__(self, pid: int, forks: List[th.Lock], mode: str,
                 waiter: Optional[FairWaiter],
                 think_range_ms: Tuple[int,int], eat_range_ms: Tuple[int,int],
                 metrics: List[Metrics],
                 state_lock: th.Lock,
                 waiting_since: List[Optional[float]],
                 last_eaten: List[float],
                 stop_event: th.Event,
                 courtesy_ms: int = 25,
                 starve_threshold_ms: int = 400):
        super().__init__(daemon=False)
        self.pid = pid
        self.N = len(forks)
        self.left = pid
        self.right = (pid+1) % self.N
        self.forks = forks
        self.mode = mode
        self.waiter = waiter
        self.think_lo, self.think_hi = think_range_ms
        self.eat_lo, self.eat_hi = eat_range_ms
        self.metrics = metrics
        self.state_lock = state_lock
        self.waiting_since = waiting_since
        self.last_eaten = last_eaten
        self.stop_event = stop_event
        self.courtesy_ms = courtesy_ms
        self.starve_thr_s = starve_threshold_ms/1000.0
        self.rnd = random.Random(0xF00D ^ pid)

    def _neighbors_starving(self) -> bool:
        """Heurística simples de cortesia: se vizinho espera há muito, cedo a vez brevemente."""
        L = (self.pid - 1) % self.N
        R = (self.pid + 1) % self.N
        nowt = now()
        with self.state_lock:
            wsL = self.waiting_since[L]
            wsR = self.waiting_since[R]
        long_L = wsL is not None and (nowt - wsL) >= self.starve_thr_s
        long_R = wsR is not None and (nowt - wsR) >= self.starve_thr_s
        return long_L or long_R

    def _acquire_forks_ordered(self) -> bool:
        """Ordem global: sempre pega primeiro garfo de menor índice (evita deadlock)."""
        a = min(self.left, self.right)
        b = max(self.left, self.right)
        # timeouts curtos permitem verificar stop_event e cortesia
        while not self.stop_event.is_set():
            # cortesia: cede se vizinho está “envelhecido”
            if self._neighbors_starving():
                msleep(self.courtesy_ms)
                continue
            if self.forks[a].acquire(timeout=0.1):
                if self.forks[b].acquire(timeout=0.1):
                    return True
                else:
                    self.forks[a].release()
            # pequena pausa para não girar forte
            msleep(5)
        return False

    def _release_forks_ordered(self):
        a = min(self.left, self.right)
        b = max(self.left, self.right)
        # libera na ordem inversa por boa prática (não é obrigatório)
        try:
            self.forks[b].release()
        finally:
            self.forks[a].release()

    def _eat_cycle(self) -> bool:
        """Um ciclo de tentar comer (retorna False se pediu para parar)."""
        # pensar
        msleep(self.rnd.randint(self.think_lo, self.think_hi))
        if self.stop_event.is_set():
            return False

        # início de espera
        start_wait = now()
        with self.state_lock:
            self.waiting_since[self.pid] = start_wait

        # admissão pelo garçom (modo 'sem') ou direto (modo 'order')
        admitted = True
        if self.mode == "sem":
            assert self.waiter is not None
            admitted = self.waiter.acquire(self.pid, self.stop_event)
            if not admitted:
                with self.state_lock:
                    self.waiting_since[self.pid] = None
                return False

        # adquirir talheres
        got = self._acquire_forks_ordered()
        with self.state_lock:
            self.waiting_since[self.pid] = None
        if not got:
            # abortou porque parou
            if self.mode == "sem" and admitted:
                self.waiter.release()
            return False

        # métrica de espera
        waited = now() - start_wait
        m = self.metrics[self.pid]
        m.meals += 1
        m.sum_wait_s += waited
        if waited > m.max_wait_s:
            m.max_wait_s = waited

        # comer
        msleep(self.rnd.randint(self.eat_lo, self.eat_hi))

        # liberar talheres e garçom
        self._release_forks_ordered()
        if self.mode == "sem" and admitted:
            self.waiter.release()

        # marca último horário em que comeu
        with self.state_lock:
            self.last_eaten[self.pid] = now()
        return True

    def run(self):
        while not self.stop_event.is_set():
            if not self._eat_cycle():
                break

# ---------------------------- Driver ------------------------------
def main():
    ap = argparse.ArgumentParser(description="Filósofos com garfos (mutex) — duas soluções anti-deadlock e métricas.")
    ap.add_argument("--n", type=int, default=5, help="Número de filósofos/garfos")
    ap.add_argument("--mode", choices=["order","sem"], default="order",
                    help="order = ordem global de talheres; sem = semáforo (garçom) FIFO")
    ap.add_argument("--limit", type=int, default=4, help="Capacidade do garçom (apenas modo 'sem')")
    ap.add_argument("--duration", type=int, default=10, help="Duração em segundos")
    ap.add_argument("--think", type=str, default="20,60", help="Intervalo ms de pensar, ex: 20,60")
    ap.add_argument("--eat", type=str, default="15,40", help="Intervalo ms de comer, ex: 15,40")
    ap.add_argument("--starve-ms", type=int, default=400, help="Limiar ms para cortesia (modo 'order')")
    ap.add_argument("--courtesy-ms", type=int, default=25, help="Quanto ceder quando vizinho está faminto (modo 'order')")
    args = ap.parse_args()

    N = max(2, args.n)
    think_lo, think_hi = [int(x) for x in args.think.split(",")]
    eat_lo, eat_hi = [int(x) for x in args.eat.split(",")]

    forks = [th.Lock() for _ in range(N)]
    metrics = [Metrics() for _ in range(N)]
    waiting_since: List[Optional[float]] = [None]*N
    last_eaten: List[float] = [0.0]*N
    state_lock = th.Lock()
    stop_event = th.Event()

    waiter = None
    if args.mode == "sem":
        cap = max(1, min(args.limit, N-1))  # típico: N-1 (p/ N=5 → 4)
        waiter = FairWaiter(capacity=cap)
        print(f"[INFO] Garçom FIFO ativo com capacidade {cap} (de {N})")

    philos = [
        Philosopher(i, forks, args.mode, waiter,
                    (think_lo, think_hi), (eat_lo, eat_hi),
                    metrics, state_lock, waiting_since, last_eaten,
                    stop_event,
                    courtesy_ms=args.courtesy_ms, starve_threshold_ms=args.starve_ms)
        for i in range(N)
    ]

    t0 = now()
    for p in philos: p.start()
    # roda por duração
    time.sleep(args.duration)
    # sinaliza fim
    stop_event.set()
    if waiter: waiter.shutdown()
    # aguarda threads (demonstra ausência de deadlock se todas retornarem)
    for p in philos: p.join(timeout=5.0)

    elapsed = now() - t0

    # ------------------- Relatório / Métricas ----------------------
    print("\n=== RESULTADOS ===")
    print(f"Modo: {'Ordem global' if args.mode=='order' else 'Semáforo FIFO'} | N={N} | Tempo={elapsed:.2f}s")
    total_meals = 0
    worst_wait = 0.0
    for i, m in enumerate(metrics):
        avg_wait = (m.sum_wait_s/m.meals if m.meals else 0.0)
        total_meals += m.meals
        worst_wait = max(worst_wait, m.max_wait_s)
        print(f"Filósofo {i}: refeições={m.meals:4d} | espera_max={m.max_wait_s*1000:6.1f} ms | espera_med={avg_wait*1000:6.1f} ms")
    print(f"Total de refeições: {total_meals} | Maior espera observada: {worst_wait*1000:.1f} ms")

    # “Provas” simples:
    # 1) Se chegamos aqui com todas as threads joinadas, não travou (sem deadlock).
    all_joined = all(not p.is_alive() for p in philos)
    print(f"Deadlock: {'NÃO' if all_joined else 'POSSÍVEL (alguma thread não retornou)'}")
    # 2) Sem “perda de itens”: cada refeição é contada exatamente uma vez pelo próprio filósofo.
    # (métrica de refeição é local à thread — logo, não há contagem duplicada)

    # Observação: para avaliar starvation, compare distribuição de 'refeições' e 'espera_max'.
    # Com 'order' + cortesia e 'sem' + FIFO, a tendência é uniformizar melhor que o ingênuo.

if __name__ == "__main__":
    main()
