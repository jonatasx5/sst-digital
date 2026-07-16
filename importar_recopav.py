"""
importar_recopav.py - Importa funcionários da RECOPAV ASFALTOS LTDA para o banco de dados.
Execute: python importar_recopav.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import banco

EMPRESA = "RECOPAV ASFALTOS LTDA"

funcionarios = [
    {"nome": "THIAGO SANTOS",           "cpf": "043.961.121-06", "cargo": "ASS. ADMINISTRATIVO"},
    {"nome": "EDER FERREIRA MACHADO",   "cpf": "011.012.991-18", "cargo": "LABORATORISTA"},
    {"nome": "RHUDSON MIGUEL",          "cpf": "054.435.991-48", "cargo": "VIGIA"},
    {"nome": "LEONTINO DE MATOS",       "cpf": "093.592.273-31", "cargo": "CALDEREIRO"},
    {"nome": "JOAO VITOR DE MATOS",     "cpf": "097.815.663-37", "cargo": "AJUDANTE DE OBRAS"},
    {"nome": "ANTONIO MATOS",           "cpf": "813.688.443-91", "cargo": "OPERADOR DE USINA"},
    {"nome": "SAYMON SANTOS",           "cpf": "710.816.551-19", "cargo": "CALDEREIRO"},
    {"nome": "LUCAS SOARES",            "cpf": "054.424.971-26", "cargo": "ENCARREGADO DE USINA"},
    {"nome": "PEDRO HENRIQUE NEVES",    "cpf": "056.354.081-84", "cargo": "FATURISTA"},
    {"nome": "LAURIMAR DE OLIVEIRA COSTA", "cpf": "707.379.252-30", "cargo": "AJUDANTE DE OBRAS"},
    {"nome": "FRANCISCO PEREIRA SANTOS","cpf": "215.461.691-72", "cargo": "PA CARREGADEIRA"},
    {"nome": "RODOLFO BARBOSA",         "cpf": "740.861.461-34", "cargo": "GERENTE"},
]

for f in funcionarios:
    f["empresa"] = EMPRESA

# Garante que a coluna empresa existe antes de importar
banco.criar_banco()

inseridos, atualizados = banco.importar_funcionarios(funcionarios)
print(f"Importação concluída: {inseridos} inserido(s), {atualizados} atualizado(s)")
print(f"Empresa: {EMPRESA}")
