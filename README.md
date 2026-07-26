# SST Digital — Sistema de Gestão de Saúde e Segurança do Trabalho

Sistema web para gestão de documentos SST (Kit Admissional, Ordens de Serviço, Treinamentos, EPI/EPC, Alojamentos, Acidentes), com envio de documentos para assinatura digital via ZapSign e Autentique.

## Stack

- **Backend**: Python 3 + FastAPI + Uvicorn
- **Banco de dados**: PostgreSQL (produção no Railway) / SQLite (local)
- **Geração de documentos**: python-docx (DOCX) + LibreOffice headless (DOCX → PDF)
- **Assinatura digital**: ZapSign API (principal) + Autentique GraphQL API (fallback automático)
- **Frontend**: HTML/CSS/JS puro (single-page, sem framework)
- **Hospedagem**: Railway (branch `main` do GitHub)

## Como rodar localmente

```bash
pip install -r requirements.txt
uvicorn app:app --reload
```

Acesse: http://localhost:8000

## Variáveis de ambiente (Railway)

| Variável | Descrição |
|---|---|
| `DATABASE_URL` | URL do PostgreSQL (Railway injeta automaticamente) |
| `SECRET_KEY` | Chave JWT para autenticação |
| `ZAPSIGN_TOKEN` | Token da API ZapSign |
| `AUTENTIQUE_TOKEN` | Token da API Autentique (fallback) |
| `AUTENTIQUE_URL` | URL GraphQL do Autentique |
| `EMPRESA` | Nome da empresa padrão nos documentos (ex: JS Construtora) |
| `RESP_SST` | Nome do responsável SST |
| `CNPJ` | CNPJ da empresa principal |

## Estrutura principal

```
app.py          — Rotas FastAPI, lógica de negócio, background tasks
banco.py        — Acesso ao banco (PostgreSQL/SQLite)
processador.py  — Geração de DOCX/PDF, leitura de planilhas
autentique.py   — Integração com API Autentique (GraphQL)
zapsign.py      — Integração com API ZapSign
config.py       — Configurações e variáveis de ambiente
index.html      — Frontend SPA (toda a interface)
modelos/        — Arquivos DOCX modelo dos documentos
```

## Módulos do sistema

- **Funcionários** — cadastro, importação via planilha Excel, suporte a múltiplas empresas
- **Modelos & Funções** — upload de modelos DOCX por cargo
- **Cadastro de EPI** — catálogo de EPIs e EPCs
- **Entrega EPI/EPC/Uniformes** — ficha de entrega com assinatura digital
- **Kit SST Envio** — envio do kit admissional completo para assinatura (ZapSign ou Autentique)
- **Treinamentos** — documentos de treinamento persistidos no banco, geração por lotação/funcionário/avulso
- **Alojamentos** — vistoria de alojamentos com assinatura
- **Acidentes** — relatório de acidentes com assinatura
- **Histórico** — acompanhamento de todos os documentos enviados, com atualização automática de status

## Empresas cadastradas

- **JS Construtora** — empresa principal (padrão)
- **RECOPAV ASFALTOS LTDA** — CNPJ 61.773.385/0001-14 (Nova Veneza/GO)
