#como rodar? 
    # Execução única
        # python ex06.py data.txt -p 4

    # Sweep com speedup (1,2,4,8) + verificação de corretude
        # python ex06.py data.txt --sweep

# -*- coding: utf-8 -*-
import argparse, mmap, os, threading as th, time
from collections import Counter
from typing import List, Tuple, Dict, Any

# -------------------------
# Util
# -------------------------
def human_time(s: float) -> str:
    return f"{s:.3f}s" if s >= 0.010 else f"{s*1e3:.2f}ms"

# -------------------------
# Particionamento por blocos (alinhado em '\n')
# -------------------------
def compute_spans(mm: mmap.mmap, parts: int) -> List[Tuple[int, int]]:
    n = len(mm)
    if parts <= 1 or n == 0:
        return [(0, n)]
    chunk = n // parts
    starts = [0]
    for i in range(1, parts):
        s = i * chunk
        if s >= n:
            s = n
        else:
            # avança até depois do próximo '\n' para alinhar em linha
            j = mm.find(b"\n", s, n)
            s = n if j == -1 else j + 1
        starts.append(s)
    starts = sorted(set(starts))
    spans = [(starts[i], starts[i+1]) for i in range(len(starts)-1)]
    # último pedaço até o final
    if not spans or spans[-1][1] < n:
        spans.append((starts[-1], n))
    # remove spans vazios
    spans = [(a,b) for (a,b) in spans if b > a]
    return spans

# -------------------------
# Map local de cada thread
# -------------------------
def worker(mm: mmap.mmap, span: Tuple[int,int], out_list: List[Any], idx: int):
    a, b = span
    pos = a
    local_sum = 0
    local_cnt = Counter()
    processed = 0
    # varre linhas dentro de [a,b)
    while pos < b:
        nl = mm.find(b"\n", pos, b)
        if nl == -1:
            line = mm[pos:b]
            pos = b
        else:
            line = mm[pos:nl]
            pos = nl + 1
        if not line:
            continue
        # remove espaços/CR
        line = line.strip()
        if not line:
            continue
        try:
            val = int(line)
        except ValueError:
            # linha inválida; ignore ou trate conforme necessário
            continue
        local_sum += val
        local_cnt[val] += 1
        processed += 1
    out_list[idx] = (local_sum, local_cnt, processed)

# -------------------------
# Reduce na thread principal
# -------------------------
def reduce_results(results: List[Tuple[int, Counter, int]]):
    total_sum = 0
    total_hist = Counter()
    total_n = 0
    for s, c, n in results:
        total_sum += s
        total_hist.update(c)
        total_n += n
    # consistência interna: |hist| somatório == contagem total
    assert sum(total_hist.values()) == total_n, "Reduce inconsistente: hist vs contagem"
    return total_sum, total_hist, total_n

# -------------------------
# Execução com P threads
# -------------------------
def run_once(path: str, P: int):
    size = os.path.getsize(path)
    if size == 0:
        return 0, Counter(), 0, 0.0

    with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        spans = compute_spans(mm, P)
        # out_list tem 1 slot por thread (um único escritor por slot → sem mutex)
        out_list: List[Any] = [None] * len(spans)
        threads: List[th.Thread] = []

        t0 = time.perf_counter()
        for i, sp in enumerate(spans):
            t = th.Thread(target=worker, args=(mm, sp, out_list, i), daemon=False)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        elapsed = time.perf_counter() - t0

        total_sum, total_hist, total_n = reduce_results(out_list)  # reduce único, sem locks
        return total_sum, total_hist, total_n, elapsed

# -------------------------
# Main / CLI
# -------------------------
def main():
    ap = argparse.ArgumentParser(description="Soma e histograma paralelos (threads) para arquivo grande de inteiros (1 por linha).")
    ap.add_argument("file", help="Arquivo de entrada (inteiros por linha)")
    ap.add_argument("-p", "--threads", type=int, default=4, help="Número de threads (P)")
    ap.add_argument("--sweep", action="store_true", help="Mede speedup para P=1,2,4,8 e valida corretude vs P=1")
    ap.add_argument("--print-top", type=int, default=0, help="Imprime os K valores mais frequentes do histograma")
    args = ap.parse_args()

    if not os.path.exists(args.file):
        raise SystemExit(f"Arquivo não encontrado: {args.file}")

    if not args.sweep:
        total_sum, hist, total_n, t = run_once(args.file, max(1, args.threads))
        print(f"Arquivo: {args.file} | P={args.threads}")
        print(f"Linhas (válidas): {total_n}")
        print(f"Soma total: {total_sum}")
        print(f"Tempo: {human_time(t)}")
        if args.print_top > 0 and hist:
            topk = hist.most_common(args.print_top)
            print(f"Top {args.print_top} frequências:")
            for val, cnt in topk:
                print(f"  {val}: {cnt}")
        return

    # Sweep P = 1,2,4,8 com validação
    Ps = [1, 2, 4, 8]
    baseline_sum = None
    baseline_hist = None
    baseline_n = None
    times = {}
    print("P,lines,sum,time_s,speedup_vs_P1")
    for P in Ps:
        total_sum, hist, total_n, t = run_once(args.file, P)
        times[P] = t
        if P == 1:
            baseline_sum = total_sum
            baseline_hist = hist
            baseline_n = total_n
            speedup = 1.0
        else:
            # Prova de corretude: resultados devem coincidir com P=1
            assert total_sum == baseline_sum, f"Soma difere vs P=1 (P={P})"
            assert hist == baseline_hist, f"Histograma difere vs P=1 (P={P})"
            assert total_n == baseline_n, f"Contagem difere vs P=1 (P={P})"
            speedup = times[1] / t if t > 0 else 0.0
        print(f"{P},{total_n},{total_sum},{t:.6f},{speedup:.3f}")

    print("\nResumo:")
    for P in Ps:
        sp = times[1] / times[P] if times[P] > 0 else 0.0
        print(f"  P={P}: {human_time(times[P])}  | speedup ≈ {sp:.2f}x")

if __name__ == "__main__":
    main()
