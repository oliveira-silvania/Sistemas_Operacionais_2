import os
import sys

# Define os parâmetros do predicado
TAMANHO_MINIMO_BYTES = 10 * 1024 * 1024  # Exemplo: 10MB
EXTENSAO_ALVO = ".log"

def apagar_seguro_walker(caminho_raiz):
    """
    Percorre o diretório recursivamente, aplicando o predicado
    (tamanho > N e extensão .log) e apaga com confirmação,
    evitando symlinks.
    """
    
    print(f"## Iniciando Walker de Remoção Segura em: {caminho_raiz}")
    arquivos_removidos = 0

    # os.walk é a maneira mais eficiente e robusta de fazer um 'walker' recursivo em Python
    for dirpath, dirnames, filenames in os.walk(caminho_raiz):
        for nome_arquivo in filenames:
            caminho_completo = os.path.join(dirpath, nome_arquivo)

            # 1. Checagem de segurança: NUNCA remover symlinks.
            if os.path.islink(caminho_completo):
                # print(f"SKIP: {caminho_completo} é um symlink.")
                continue

            # 2. Checagem de predicado: Extensão .log
            if not nome_arquivo.lower().endswith(EXTENSAO_ALVO):
                continue
            
            # 3. Checagem de predicado: Tamanho > N
            try:
                tamanho = os.path.getsize(caminho_completo)
            except OSError:
                print(f"AVISO: Não foi possível obter o tamanho de {caminho_completo}.", file=sys.stderr)
                continue

            if tamanho > TAMANHO_MINIMO_BYTES:
                # Predicado satisfeito!
                
                # 4. Remoção Segura (Confirmação no stdout e remoção)
                print("-" * 40)
                print(f"**PREDICADO ATENDIDO:**")
                print(f"  Arquivo: {caminho_completo}")
                print(f"  Tamanho: {tamanho:,} bytes ( > {TAMANHO_MINIMO_BYTES:,} bytes)")
                
                try:
                    # SIMULANDO a confirmação para evitar apagar arquivos reais
                    # Para apagar de verdade, descomente a linha abaixo:
                    # os.remove(caminho_completo) 
                    print(f"[SIMULADO] REMOVIDO: {caminho_completo}")
                    arquivos_removidos += 1
                except OSError as e:
                    print(f"ERRO: Falha ao remover {caminho_completo}: {e}", file=sys.stderr)
                
    print("-" * 40)
    print(f"## Walker concluído. {arquivos_removidos} arquivos elegíveis processados (Simulado).")

# Exemplo de execução (use um caminho de teste seguro!)
# if __name__ == '__main__':
#     # O caminho deve ser um diretório existente (ex: /tmp/logs)
#     apagar_seguro_walker('.')