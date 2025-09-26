#como rodar?
#     python ex01.py
#
# Agora a quantidade de cavalos é fixa (=5). Os demais parâmetros
# (tamanho de pista, passos e delay) ainda são sorteados automaticamente.
# O usuário só informa a aposta.

# -*- coding: utf-8 -*-
import threading
import random
import time

# =========================
# Configuração fixa
# =========================
NUM_CAVALOS_FIXO = 5  # SEMPRE 5 cavalos competindo

# =========================
# Intervalos de sorteio (demais parâmetros)
# =========================
RANGE_TAMANHO_PISTA  = (40, 80)    # de 40 a 80 casas
RANGE_PASSO_MIN      = (1, 3)      # passo mínimo entre 1 e 3
RANGE_PASSO_MAX      = (4, 8)      # passo máximo entre 4 e 8 (>= passo_min)
RANGE_DELAY_MS       = (50, 200)   # delay visual por tick entre 50 e 200 ms

# =========================
# Utils
# =========================
def ler_inteiro(prompt, minimo=None, maximo=None):
    """Lê um inteiro do usuário com validação de faixa."""
    while True:
        s = input(prompt).strip()
        try:
            v = int(s)
            if minimo is not None and v < minimo:
                print(f"Valor deve ser >= {minimo}.")
                continue
            if maximo is not None and v > maximo:
                print(f"Valor deve ser <= {maximo}.")
                continue
            return v
        except ValueError:
            print("Digite um número inteiro válido.")

def barra_progresso(pos, tamanho, largura=50):
    proporcao = max(0.0, min(1.0, pos / float(tamanho)))
    blocos = int(round(proporcao * largura))
    return "[" + "#" * blocos + "-" * (largura - blocos) + "]"

def imprimir_placar(positions, tamanho_pista, vencedor, finish_order):
    print("\n" + "=" * 70)
    for i, p in enumerate(positions):
        p_clip = min(p, tamanho_pista)
        print(f"Cavalo {i+1:02d} {barra_progresso(p_clip, tamanho_pista)} {p_clip:>3}/{tamanho_pista}")
    if vencedor is None:
        print("Status: corrida em andamento...")
    else:
        print(f"Vencedor: Cavalo {vencedor+1}")
    if finish_order:
        podium = ", ".join(f"{idx+1}º: Cavalo {h+1}" for idx, h in enumerate(finish_order))
        print("Chegada (ordem): " + podium)
    print("=" * 70)

# =========================
# Lógica principal
# =========================
def main():
    print("=== Corrida de Cavalos (Threads) ===")

    rng = random.Random()

    # -------- Parâmetros --------
    num_cavalos   = NUM_CAVALOS_FIXO  # FIXO = 5
    tamanho_pista = rng.randint(*RANGE_TAMANHO_PISTA)
    passo_min     = rng.randint(*RANGE_PASSO_MIN)
    passo_max_low  = max(RANGE_PASSO_MAX[0], passo_min + 1)
    passo_max_high = max(passo_max_low, RANGE_PASSO_MAX[1])
    passo_max     = rng.randint(passo_max_low, passo_max_high)
    delay_tick    = rng.randint(*RANGE_DELAY_MS) / 1000.0

    print("\nParâmetros da corrida:")
    print(f"- Número de cavalos : {num_cavalos} (fixo)")
    print(f"- Tamanho da pista  : {tamanho_pista}")
    print(f"- Passo por tick    : [{passo_min}, {passo_max}]")
    print(f"- Delay por tick    : {int(delay_tick*1000)} ms")

    print("\nCavalos numerados de 1 a", num_cavalos)
    aposta = ler_inteiro("Sua aposta (número do cavalo): ", minimo=1, maximo=num_cavalos) - 1

    # -------- Estado compartilhado --------
    positions = [0 for _ in range(num_cavalos)]
    steps = [0 for _ in range(num_cavalos)]
    finished = [False for _ in range(num_cavalos)]
    finish_order = []
    vencedor = {"idx": None}
    race_over = threading.Event()

    # -------- Sincronizações --------
    start_barrier  = threading.Barrier(num_cavalos)
    choose_barrier = threading.Barrier(num_cavalos)
    apply_barrier  = threading.Barrier(num_cavalos)
    state_lock = threading.Lock()

    def cavalo_thread(horse_id: int):
        try:
            start_barrier.wait()
        except threading.BrokenBarrierError:
            return

        trng = random.Random()
        while not race_over.is_set():
            steps[horse_id] = trng.randint(passo_min, passo_max)

            try:
                choose_barrier.wait()
            except threading.BrokenBarrierError:
                break

            if horse_id == 0:
                with state_lock:
                    for i in range(num_cavalos):
                        if not finished[i]:
                            positions[i] += steps[i]
                            if positions[i] >= tamanho_pista:
                                finished[i] = True
                                finish_order.append(i)
                                if vencedor["idx"] is None:
                                    vencedor["idx"] = i
                    imprimir_placar(positions, tamanho_pista, vencedor["idx"], finish_order)
                    if vencedor["idx"] is not None:
                        race_over.set()
                if delay_tick > 0:
                    time.sleep(delay_tick)

            try:
                apply_barrier.wait()
            except threading.BrokenBarrierError:
                break

    # Criar e iniciar threads de cavalos
    threads = [threading.Thread(target=cavalo_thread, args=(i,), daemon=True) for i in range(num_cavalos)]

    print("\nPreparar... Apontar...")
    time.sleep(0.6)
    print("LARGARAM!\n")

    for t in threads:
        t.start()

    while not race_over.is_set():
        time.sleep(0.05)

    for b in (choose_barrier, apply_barrier):
        try:
            b.abort()
        except:
            pass

    for t in threads:
        t.join(timeout=0.5)

    # Resultado final
    vencedor_idx = vencedor["idx"]
    print("\n" + "#" * 70)
    print("RESULTADO FINAL")
    if vencedor_idx is not None:
        print(f"Vencedor: Cavalo {vencedor_idx+1}")
        if aposta == vencedor_idx:
            print("Parabéns! 🎉 Sua aposta foi CORRETA.")
        else:
            print(f"Que pena! Sua aposta foi no Cavalo {aposta+1}.")
    else:
        print("Nenhum vencedor definido (algo inesperado ocorreu).")

    if finish_order:
        texto_ordem = ", ".join(f"{pos+1}º: Cavalo {h+1}" for pos, h in enumerate(finish_order))
        print("Ordem de chegada: " + texto_ordem)
    else:
        print("Nenhum cavalo registrado na chegada.")
    print("#" * 70)

if __name__ == "__main__":
    main()
