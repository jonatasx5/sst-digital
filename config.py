"""
Configurações do sistema SST Digital
Edite este arquivo com os dados da sua empresa.
"""

# ─── AUTENTIQUE ───────────────────────────────────────────
AUTENTIQUE_TOKEN = "5970c128466b61a704bfd03819b788d0323790a15467d377e8bc5897a7e77155"
AUTENTIQUE_URL   = "https://api.autentique.com.br/v2/graphql"

# ─── EMPRESA ──────────────────────────────────────────────
EMPRESA   = "JS Construtora"          # Nome da empresa nos documentos
RESP_SST  = ""                        # Nome do responsável SST (preencha)
CNPJ      = ""                        # CNPJ da empresa (opcional)

# ─── DOCUMENTOS ───────────────────────────────────────────
# Lista com os 14 documentos disponíveis
# "id"     → nome do arquivo .docx na pasta modelos/ (sem extensão)
# "nome"   → nome exibido na interface
# "obrig"  → se True, vai para todas as funções por padrão

DOCUMENTOS = [
    {"id": "01_treinamento_admissional",          "nome": "01 - Treinamento Admissional",                                    "obrig": True},
    {"id": "02_politica_de_seguranca",            "nome": "02 - Política de Segurança",                                      "obrig": True},
    {"id": "03_os_ajudante",                      "nome": "03 - OS Ajudante",                                                "obrig": False},
    {"id": "04_treinamento_nr06",                 "nome": "04 - Treinamento NR 06",                                          "obrig": True},
    {"id": "05_treinamento_nr18",                 "nome": "05 - Treinamento NR 18",                                          "obrig": False},
    {"id": "06_treinamento_nr21",                 "nome": "06 - Treinamento NR 21",                                          "obrig": False},
    {"id": "07_treinamento_nr26",                 "nome": "07 - Treinamento NR 26",                                          "obrig": False},
    {"id": "08_treinamento_nr12",                 "nome": "08 - Treinamento NR 12",                                          "obrig": False},
    {"id": "09_treinamento_produtos_quimicos",    "nome": "09 - Treinamento Produtos Químicos",                              "obrig": False},
    {"id": "10_ficha_controle_epi",               "nome": "10 - Ficha de Controle de EPI",                                  "obrig": True},
    {"id": "11_pop",                              "nome": "11 - POP",                                                        "obrig": False},
    {"id": "12_treinamento_direcao_defensiva",    "nome": "12 - Treinamento Direção Defensiva",                             "obrig": False},
    {"id": "13_treinamento_vias_urbanas",         "nome": "13 - Treinamento Trabalho em Vias Urbanas, Estaduais e Federais","obrig": False},
    {"id": "14_primeiros_socorros",               "nome": "14 - Primeiros Socorros",                                        "obrig": False},
]

# ─── PASTAS ───────────────────────────────────────────────
import os
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
MODELOS_DIR = os.path.join(BASE_DIR, "modelos")
OUTPUT_DIR  = os.path.join(BASE_DIR, "output")
DB_PATH     = os.path.join(BASE_DIR, "sst.db")
