from datetime import datetime
from pathlib import Path
import os
from extensao import *

ArquivoLOG = None


def IniciarLog():
    global ArquivoLOG

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ArquivoLOG = Path(f"{PastaLog}RAG_{timestamp}.log")

    # cria arquivo vazio
    ArquivoLOG.write_text("=== INICIO EXECUÇÃO RAG ===\n")
    sep = "═" * 70
    with open(ArquivoLOG, "w", encoding="utf-8") as f:
        f.write(f"{sep}\n")
        f.write(f"  PetroServ — RAG Text-to-SQL · Log de Sessão\n")
        f.write(f"  Arquivo  : {os.path.basename(ArquivoLOG)}\n")
        f.write(f"  Iniciado : {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")
        f.write(f"{sep}\n\n")

def GerarLog(msg: str):
    global ArquivoLOG

    linha = f"{datetime.now():%H:%M:%S} | {msg}"

    print(linha, flush=True)

    if ArquivoLOG is None:
        return

    
    

    with open(ArquivoLOG, "a", encoding="utf-8") as f:
        f.write(linha + "\n")

    #-- depois que colocar a mensagem no arquivo de log, mostra no video
    print(linha)


def FinalizarLog():
    global ArquivoLOG

    if ArquivoLOG:
        with open(ArquivoLOG, "a", encoding="utf-8") as f:
            f.write("=== FIM EXECUÇÃO ===\n")

        ArquivoLOG = None
