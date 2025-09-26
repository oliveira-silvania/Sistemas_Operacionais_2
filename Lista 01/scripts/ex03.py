#como rodar? 
    # COM trava (correto)
        # python ex03.py -m 32 -t 8 -n 200000 -i 1000

    # SEM trava (incorreto, para evidenciar corridas)
        # python ex03.py -m 32 -t 8 -n 200000 -i 1000 --nolock

# -*- coding: utf-8 -*-
import argparse, random, threading as th, time
from typing import List

class Bank:
    def __init__(self, m: int, init_per_ac: int, use_lock: bool):
        self.m = m
        self.bal: List[int] = [init_per_ac for _ in range(m)]
        self.locks: List[th.Lock] = [th.Lock() for _ in range(m)]
        self.metrics_lock = th.Lock()
        self.use_lock = use_lock
        self.total_initial = sum(self.bal)
        self.running = True
        # métricas
        self.transfers_ok = 0
        self.transfers_skipped = 0
        self.audit_divergences = 0
        self.audit_checks = 0

    # snapshot consistente (trava todas as contas em ordem)
    def _sum_balances_locked(self) -> int:
        for i in range(self.m):
            self.locks[i].acquire()
        try:
            return sum(self.bal)
        finally:
            for i in reversed(range(self.m)):
                self.locks[i].release()

    def sum_balances(self) -> int:
        return self._sum_balances_locked() if self.use_lock else sum(self.bal)

    def transfer(self, src: int, dst: int, amount: int, chaos_sleep: bool):
        if src == dst: 
            return
        if self.use_lock:
            a, b = (src, dst) if src < dst else (dst, src)
            L1, L2 = self.locks[a], self.locks[b]
            with L1:
                with L2:
                    if self.bal[src] >= amount:
                        self.bal[src] -= amount
                        self.bal[dst] += amount
                        with self.metrics_lock:
                            self.transfers_ok += 1
                    else:
                        with self.metrics_lock:
                            self.transfers_skipped += 1
        else:
            # sem trava: suscetível a perdas de atualização
            if self.bal[src] >= amount:
                if chaos_sleep:
                    time.sleep(random.uniform(0.0, 0.00005))
                self.bal[src] -= amount
                if chaos_sleep:
                    time.sleep(random.uniform(0.0, 0.00005))
                self.bal[dst] += amount
                with self.metrics_lock:
                    self.transfers_ok += 1
            else:
                with self.metrics_lock:
                    self.transfers_skipped += 1

def worker_thread(bank: Bank, ops: int, max_amount: int, chaos_sleep: bool):
    rnd = random.Random(time.time_ns() ^ th.get_ident())
    m = bank.m
    for _ in range(ops):
        src = rnd.randrange(m)
        dst = rnd.randrange(m)
        while dst == src:
            dst = rnd.randrange(m)
        amount = rnd.randint(1, max_amount)
        bank.transfer(src, dst, amount, chaos_sleep)

def auditor_thread(bank: Bank, period_ms: int):
    while bank.running:
        time.sleep(period_ms / 1000.0)
        total = bank.sum_balances()
        bank.audit_checks += 1
        if bank.use_lock:
            # no modo correto, o invariante deve SEMPRE valer
            assert total == bank.total_initial, (
                f"Invariante violado: total={total} != inicial={bank.total_initial}"
            )
        else:
            # no modo incorreto, contamos divergências observadas
            if total != bank.total_initial:
                bank.audit_divergences += 1

def main():
    ap = argparse.ArgumentParser(description="Contas bancárias com transferências concorrentes (Python threads)")
    ap.add_argument("-m", "--accounts", type=int, default=32, help="Número de contas M")
    ap.add_argument("-t", "--threads", type=int, default=8, help="Número de threads T")
    ap.add_argument("-n", "--ops", type=int, default=200000, help="Operações por thread")
    ap.add_argument("-i", "--init", type=int, default=1000, help="Saldo inicial por conta")
    ap.add_argument("--max-amount", type=int, default=10, help="Valor máximo por transferência")
    ap.add_argument("--audit-ms", type=int, default=20, help="Período do auditor (ms)")
    ap.add_argument("--nolock", action="store_true", help="Executar sem travas (demonstra corrida)")
    args = ap.parse_args()

    use_lock = not args.nolock
    bank = Bank(args.accounts, args.init, use_lock=use_lock)

    print(f"[INFO] M={args.accounts} | T={args.threads} | ops/thread={args.ops} | init/conta={args.init} | "
          f"{'COM TRAVA' if use_lock else 'SEM TRAVA'}")

    aud = th.Thread(target=auditor_thread, args=(bank, args.audit_ms), daemon=True)
    aud.start()

    workers = []
    start = time.perf_counter()
    for _ in range(args.threads):
        w = th.Thread(target=worker_thread,
                      args=(bank, args.ops, args.max_amount, not use_lock),
                      daemon=False)
        workers.append(w)
        w.start()
    for w in workers:
        w.join()
    elapsed = time.perf_counter() - start

    # encerrar auditor e tirar totais finais de forma consistente
    bank.running = False
    aud.join()

    # soma final consistente (trava geral mesmo no modo sem trava)
    for i in range(bank.m):
        bank.locks[i].acquire()
    total_final = sum(bank.bal)
    for i in reversed(range(bank.m)):
        bank.locks[i].release()

    throughput = (args.threads * args.ops) / elapsed if elapsed > 0 else 0.0

    print("\n=== RESULTADOS ===")
    print(f"Modo:         {'COM TRAVA' if use_lock else 'SEM TRAVA'}")
    print(f"Total inicial:{bank.total_initial}")
    print(f"Total final:  {total_final}")
    print(f"Tempo:        {elapsed:.3f}s")
    print(f"Throughput:   {throughput:,.0f} ops/s")
    print(f"OK:           {bank.transfers_ok:,} | Skips (sem saldo): {bank.transfers_skipped:,}")
    if use_lock:
        assert total_final == bank.total_initial, "Invariante falhou no modo COM TRAVA"
        print("✅ Invariante mantido: soma global constante (assert passou).")
    else:
        print(f"Divergências observadas pelo auditor: {bank.audit_divergences}/{bank.audit_checks} snapshots "
              f"({(100*bank.audit_divergences/bank.audit_checks if bank.audit_checks else 0):.1f}%).")
        if total_final != bank.total_initial:
            print("⚠️  Corrida evidenciada: soma final difere da inicial (como esperado SEM TRAVA).")
        else:
            print("ℹ️  Soma final igual nesta execução; aumente -t e -n para ver divergências persistentes.")

if __name__ == "__main__":
    main()

