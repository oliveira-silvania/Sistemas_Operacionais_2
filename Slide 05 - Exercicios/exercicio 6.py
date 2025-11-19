import os
import sys

def du_simplificado(caminho='.'):
    """
    Calcula e lista tamanhos reais (st_blocks*512) e lógicos (st_size),
    destacando arquivos sparse.
    """
    total_logico = 0
    total_real = 0

    print(f"## Análise de Tamanhos em: {caminho}")
    print("-" * 50)
    print(f"{'Nome':<30} | {'Lógico (B)':>15} | {'Real (B)':>15} | {'Sparse'}")
    print("-" * 50)

    try:
        for nome in os.listdir(caminho):
            caminho_completo = os.path.join(caminho, nome)

            # Usamos lstat para evitar seguir links simbólicos e obter o stat dele.
            # O enunciado sugere focar em arquivos.
            try:
                stat_info = os.lstat(caminho_completo)
            except OSError:
                # Ignora arquivos inacessíveis (permissão negada, etc.)
                continue

            # Processa apenas arquivos (ignora diretórios, pipes, sockets, etc.)
            if not os.path.isfile(caminho_completo):
                continue
            
            # st_blocks * 512 (bytes)
            # Nota: O tamanho do bloco pode variar, mas 512 é o padrão POSIX para st_blocks.
            tamanho_real = stat_info.st_blocks * 512 
            tamanho_logico = stat_info.st_size
            
            total_logico += tamanho_logico
            total_real += tamanho_real
            
            # Verificação de arquivo Sparse:
            # Tamanho lógico é maior que o tamanho real (mais que 1 bloco de diferença para ser notável)
            sparse_flag = "SIM" if tamanho_logico > tamanho_real and tamanho_real > 0 else "NÃO"
            
            print(f"{nome:<30} | {tamanho_logico:>15,} | {tamanho_real:>15,} | {sparse_flag}")
            
    except Exception as e:
        print(f"\nErro ao processar {caminho}: {e}", file=sys.stderr)
        return

    print("-" * 50)
    print(f"**Total Lógico:** {total_logico:,} bytes")
    print(f"**Total Real (em Disco):** {total_real:,} bytes")

# Exemplo de execução (pode ser executado com um caminho específico)
# if __name__ == '__main__':
#     du_simplificado('/caminho/do/diretorio') # ou '.' para o diretório atual