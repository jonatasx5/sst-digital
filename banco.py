"""
banco.py - Banco de dados com suporte a PostgreSQL e SQLite

Usa PostgreSQL quando DATABASE_URL está disponível (Railway)
Usa SQLite como fallback local
"""

import os
import sqlite3

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Detecta se usa PostgreSQL ou SQLite
USE_POSTGRES = bool(DATABASE_URL and DATABASE_URL.startswith(("postgres://", "postgresql://")))

# Import lazy — não falha na inicialização se psycopg2 não estiver disponível
_psycopg2 = None
_psycopg2_extras = None

if USE_POSTGRES:
    try:
        import psycopg2 as _psycopg2
        import psycopg2.extras as _psycopg2_extras
        print("✅ psycopg2 carregado — usando PostgreSQL")
    except ImportError as e:
        print(f"⚠️  psycopg2 não disponível ({e}) — usando SQLite")
        USE_POSTGRES = False


def conectar():
    if USE_POSTGRES:
        conn = _psycopg2.connect(DATABASE_URL)
        return conn
    else:
        from config import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def executar(query, params=(), fetchone=False, fetchall=False, commit=False):
    """Executa query compatível com PostgreSQL e SQLite."""
    if USE_POSTGRES:
        query = query.replace("?", "%s")
        query = query.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        query = query.replace("datetime('now','localtime')", "NOW()")
        query = query.replace("INSERT OR IGNORE", "INSERT ON CONFLICT DO NOTHING")
        query = query.replace("OR IGNORE", "ON CONFLICT DO NOTHING")

    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
        else:
            cur = conn.cursor()

        cur.execute(query, params)
        result = None

        if fetchone:
            row = cur.fetchone()
            result = dict(row) if row else None
        elif fetchall:
            rows = cur.fetchall()
            result = [dict(r) for r in rows]

        if commit:
            conn.commit()

        if USE_POSTGRES and not fetchone and not fetchall:
            try:
                result = cur.fetchone()
                if result:
                    result = dict(result)
            except Exception:
                pass

        return result
    finally:
        conn.close()


def criar_banco():
    """Cria todas as tabelas."""
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS funcionarios (
                    id SERIAL PRIMARY KEY,
                    nome TEXT NOT NULL,
                    cpf TEXT NOT NULL UNIQUE,
                    matricula TEXT,
                    cargo TEXT NOT NULL,
                    lotacao TEXT,
                    admissao TEXT,
                    celular TEXT,
                    email TEXT,
                    ativo INTEGER DEFAULT 1,
                    criado_em TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS funcao_documentos (
                    id SERIAL PRIMARY KEY,
                    cargo TEXT NOT NULL,
                    doc_id TEXT NOT NULL,
                    UNIQUE(cargo, doc_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS lotes (
                    id SERIAL PRIMARY KEY,
                    descricao TEXT,
                    criado_em TIMESTAMP DEFAULT NOW(),
                    status TEXT DEFAULT 'pendente'
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS lote_funcionarios (
                    id SERIAL PRIMARY KEY,
                    lote_id INTEGER REFERENCES lotes(id),
                    funcionario_id INTEGER REFERENCES funcionarios(id),
                    status TEXT DEFAULT 'pendente',
                    UNIQUE(lote_id, funcionario_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS envios (
                    id SERIAL PRIMARY KEY,
                    lote_funcionario_id INTEGER REFERENCES lote_funcionarios(id),
                    funcionario_id INTEGER REFERENCES funcionarios(id),
                    doc_id TEXT NOT NULL,
                    doc_nome TEXT,
                    pdf_path TEXT,
                    autentique_id TEXT,
                    link_assinatura TEXT,
                    status TEXT DEFAULT 'pendente',
                    enviado_em TIMESTAMP,
                    assinado_em TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS modelos (
                    pk SERIAL PRIMARY KEY,
                    id TEXT NOT NULL,
                    nome TEXT NOT NULL,
                    conteudo BYTEA,
                    cargo TEXT DEFAULT NULL,
                    criado_em TIMESTAMP DEFAULT NOW(),
                    UNIQUE(id, cargo)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS documentos_extras (
                    id TEXT PRIMARY KEY,
                    nome TEXT NOT NULL,
                    kit_padrao BOOLEAN DEFAULT FALSE,
                    criado_em TIMESTAMP DEFAULT NOW()
                )
            """)
            conn.commit()
        else:
            cur = conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS funcionarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL,
                cpf TEXT NOT NULL UNIQUE, matricula TEXT, cargo TEXT NOT NULL,
                lotacao TEXT, admissao TEXT, celular TEXT, email TEXT,
                ativo INTEGER DEFAULT 1, criado_em TEXT DEFAULT (datetime('now','localtime')))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS funcao_documentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT, cargo TEXT NOT NULL,
                doc_id TEXT NOT NULL, UNIQUE(cargo, doc_id))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS lotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, descricao TEXT,
                criado_em TEXT DEFAULT (datetime('now','localtime')), status TEXT DEFAULT 'pendente')""")
            cur.execute("""CREATE TABLE IF NOT EXISTS lote_funcionarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT, lote_id INTEGER, funcionario_id INTEGER,
                status TEXT DEFAULT 'pendente', UNIQUE(lote_id, funcionario_id))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS envios (
                id INTEGER PRIMARY KEY AUTOINCREMENT, lote_funcionario_id INTEGER,
                funcionario_id INTEGER, doc_id TEXT NOT NULL, doc_nome TEXT,
                pdf_path TEXT, autentique_id TEXT, link_assinatura TEXT,
                status TEXT DEFAULT 'pendente', enviado_em TEXT, assinado_em TEXT)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS modelos (
                pk INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL,
                nome TEXT NOT NULL,
                conteudo BLOB,
                cargo TEXT DEFAULT NULL,
                criado_em TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(id, cargo))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS documentos_extras (
                id TEXT PRIMARY KEY,
                nome TEXT NOT NULL,
                kit_padrao INTEGER DEFAULT 0,
                criado_em TEXT DEFAULT (datetime('now','localtime')))""")
            conn.commit()

        # Tabela catálogo de EPIs
        if USE_POSTGRES:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS catalogo_epis (
                    id SERIAL PRIMARY KEY,
                    descricao TEXT NOT NULL,
                    ca TEXT DEFAULT '',
                    quantidade_padrao INTEGER DEFAULT 1,
                    ativo INTEGER DEFAULT 1,
                    criado_em TIMESTAMP DEFAULT NOW(),
                    UNIQUE(descricao)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cargo_epis (
                    id SERIAL PRIMARY KEY,
                    cargo TEXT NOT NULL,
                    epi_id INTEGER REFERENCES catalogo_epis(id) ON DELETE CASCADE,
                    quantidade INTEGER DEFAULT 1,
                    UNIQUE(cargo, epi_id)
                )
            """)
        else:
            cur.execute("""CREATE TABLE IF NOT EXISTS catalogo_epis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                descricao TEXT NOT NULL UNIQUE,
                ca TEXT DEFAULT '',
                quantidade_padrao INTEGER DEFAULT 1,
                ativo INTEGER DEFAULT 1,
                criado_em TEXT DEFAULT (datetime('now','localtime')))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS cargo_epis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cargo TEXT NOT NULL,
                epi_id INTEGER REFERENCES catalogo_epis(id) ON DELETE CASCADE,
                quantidade INTEGER DEFAULT 1,
                UNIQUE(cargo, epi_id))""")
        conn.commit()

        # Tabela CBO por cargo
        if USE_POSTGRES:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cargo_cbo (
                    id SERIAL PRIMARY KEY,
                    cargo TEXT NOT NULL UNIQUE,
                    cbo_codigo TEXT DEFAULT '',
                    cbo_titulo TEXT DEFAULT '',
                    cbo_descricao TEXT DEFAULT '',
                    atualizado_em TIMESTAMP DEFAULT NOW()
                )
            """)
        else:
            cur.execute("""CREATE TABLE IF NOT EXISTS cargo_cbo (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cargo TEXT NOT NULL UNIQUE,
                cbo_codigo TEXT DEFAULT '',
                cbo_titulo TEXT DEFAULT '',
                cbo_descricao TEXT DEFAULT '',
                atualizado_em TEXT DEFAULT (datetime('now','localtime')))""")
        conn.commit()

        # Tabela PGR por cargo
        if USE_POSTGRES:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pgr_inventario (
                    id SERIAL PRIMARY KEY,
                    cargo TEXT NOT NULL UNIQUE,
                    cbo TEXT DEFAULT '',
                    ambiente TEXT DEFAULT '',
                    atividades TEXT DEFAULT '',
                    riscos TEXT DEFAULT '',
                    epis TEXT DEFAULT '',
                    epcs TEXT DEFAULT '',
                    atualizado_em TIMESTAMP DEFAULT NOW()
                )
            """)
        else:
            cur.execute("""CREATE TABLE IF NOT EXISTS pgr_inventario (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cargo TEXT NOT NULL UNIQUE,
                cbo TEXT DEFAULT '',
                ambiente TEXT DEFAULT '',
                atividades TEXT DEFAULT '',
                riscos TEXT DEFAULT '',
                epis TEXT DEFAULT '',
                epcs TEXT DEFAULT '',
                atualizado_em TEXT DEFAULT (datetime('now','localtime')))""")
        conn.commit()

        # Tabela usuarios
        if USE_POSTGRES:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS usuarios (
                    id SERIAL PRIMARY KEY,
                    nome TEXT NOT NULL,
                    login TEXT NOT NULL UNIQUE,
                    senha_hash TEXT NOT NULL,
                    perfil TEXT NOT NULL DEFAULT 'usuario',
                    permissoes TEXT NOT NULL DEFAULT '[]',
                    ativo INTEGER NOT NULL DEFAULT 1,
                    criado_em TIMESTAMP DEFAULT NOW()
                )
            """)
        else:
            cur.execute("""CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                login TEXT NOT NULL UNIQUE,
                senha_hash TEXT NOT NULL,
                perfil TEXT NOT NULL DEFAULT 'usuario',
                permissoes TEXT NOT NULL DEFAULT '[]',
                ativo INTEGER NOT NULL DEFAULT 1,
                criado_em TEXT DEFAULT (datetime('now','localtime')))""")
        conn.commit()

        # Tabela engenheiros
        if USE_POSTGRES:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS engenheiros (
                    id SERIAL PRIMARY KEY,
                    nome TEXT NOT NULL,
                    crea TEXT DEFAULT '',
                    ativo INTEGER DEFAULT 1,
                    criado_em TIMESTAMP DEFAULT NOW()
                )
            """)
        else:
            cur.execute("""CREATE TABLE IF NOT EXISTS engenheiros (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                crea TEXT DEFAULT '',
                ativo INTEGER DEFAULT 1,
                criado_em TEXT DEFAULT (datetime('now','localtime')))""")
        conn.commit()

        # Tabelas de Alojamentos
        if USE_POSTGRES:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS alojamento_vistorias (
                    id SERIAL PRIMARY KEY,
                    frente_servico TEXT DEFAULT '',
                    contrato TEXT DEFAULT '',
                    localizacao TEXT DEFAULT '',
                    data_vistoria TEXT DEFAULT '',
                    num_trabalhadores INTEGER DEFAULT 0,
                    responsavel TEXT DEFAULT '',
                    cargo_responsavel TEXT DEFAULT '',
                    encarregado TEXT DEFAULT '',
                    cel_encarregado TEXT DEFAULT '',
                    resultado TEXT DEFAULT 'conforme',
                    prazo_regularizacao TEXT DEFAULT '',
                    observacao_geral TEXT DEFAULT '',
                    assinatura_responsavel TEXT DEFAULT '',
                    assinatura_encarregado TEXT DEFAULT '',
                    criado_por TEXT DEFAULT '',
                    criado_em TIMESTAMP DEFAULT NOW(),
                    link_assinatura TEXT DEFAULT '',
                    zapsign_token TEXT DEFAULT ''
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS alojamento_itens (
                    id SERIAL PRIMARY KEY,
                    vistoria_id INTEGER REFERENCES alojamento_vistorias(id) ON DELETE CASCADE,
                    bloco INTEGER,
                    item_num TEXT,
                    descricao TEXT,
                    status TEXT DEFAULT 'na',
                    observacao TEXT DEFAULT ''
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS alojamento_fotos (
                    id SERIAL PRIMARY KEY,
                    vistoria_id INTEGER REFERENCES alojamento_vistorias(id) ON DELETE CASCADE,
                    nome_arquivo TEXT,
                    dados_base64 TEXT,
                    criado_em TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS alojamento_plano_acao (
                    id SERIAL PRIMARY KEY,
                    vistoria_id INTEGER REFERENCES alojamento_vistorias(id) ON DELETE CASCADE,
                    num_nc TEXT DEFAULT '',
                    descricao TEXT DEFAULT '',
                    responsavel TEXT DEFAULT '',
                    prazo TEXT DEFAULT '',
                    status_acao TEXT DEFAULT 'pendente'
                )
            """)
        else:
            cur.execute("""CREATE TABLE IF NOT EXISTS alojamento_vistorias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                frente_servico TEXT DEFAULT '',
                contrato TEXT DEFAULT '',
                localizacao TEXT DEFAULT '',
                data_vistoria TEXT DEFAULT '',
                num_trabalhadores INTEGER DEFAULT 0,
                responsavel TEXT DEFAULT '',
                cargo_responsavel TEXT DEFAULT '',
                encarregado TEXT DEFAULT '',
                cel_encarregado TEXT DEFAULT '',
                resultado TEXT DEFAULT 'conforme',
                prazo_regularizacao TEXT DEFAULT '',
                observacao_geral TEXT DEFAULT '',
                assinatura_responsavel TEXT DEFAULT '',
                assinatura_encarregado TEXT DEFAULT '',
                criado_por TEXT DEFAULT '',
                criado_em TEXT DEFAULT (datetime('now','localtime')),
                link_assinatura TEXT DEFAULT '',
                zapsign_token TEXT DEFAULT '')""")
            cur.execute("""CREATE TABLE IF NOT EXISTS alojamento_itens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vistoria_id INTEGER REFERENCES alojamento_vistorias(id),
                bloco INTEGER,
                item_num TEXT,
                descricao TEXT,
                status TEXT DEFAULT 'na',
                observacao TEXT DEFAULT '')""")
            cur.execute("""CREATE TABLE IF NOT EXISTS alojamento_fotos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vistoria_id INTEGER REFERENCES alojamento_vistorias(id),
                nome_arquivo TEXT,
                dados_base64 TEXT,
                criado_em TEXT DEFAULT (datetime('now','localtime')))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS alojamento_plano_acao (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vistoria_id INTEGER REFERENCES alojamento_vistorias(id),
                num_nc TEXT DEFAULT '',
                descricao TEXT DEFAULT '',
                responsavel TEXT DEFAULT '',
                prazo TEXT DEFAULT '',
                status_acao TEXT DEFAULT 'pendente')""")

        # Migrações: adiciona colunas novas se ainda não existirem
        if USE_POSTGRES:
            conn.commit()  # fecha transação anterior antes das migrações DDL
            conn.autocommit = True
            for col, defval in [('cel_encarregado', "''"), ('link_assinatura', "''"), ('zapsign_token', "''")]:
                try:
                    cur.execute(f"ALTER TABLE alojamento_vistorias ADD COLUMN {col} TEXT DEFAULT {defval}")
                except Exception:
                    pass  # coluna já existe
            conn.autocommit = False
        else:
            cols_existentes = [r[1] for r in cur.execute("PRAGMA table_info(alojamento_vistorias)").fetchall()]
            for col, defval in [('cel_encarregado', "''"), ('link_assinatura', "''"), ('zapsign_token', "''")]:
                if col not in cols_existentes:
                    cur.execute(f"ALTER TABLE alojamento_vistorias ADD COLUMN {col} TEXT DEFAULT {defval}")
            conn.commit()

        # Migrações: colunas novas em acidentes_relatorios
        _acid_new_cols = [('descricao_acidente',"''"),('cel_testemunha1',"''"),('cel_testemunha2',"''"),
                          ('cel_supervisor',"''"),('responsavel_nome',"''"),('responsavel_cpf',"''"),
                          ('empresa_terceiro',"''"),('cel_tecnico',"''")]
        if USE_POSTGRES:
            conn.autocommit = True
            for col, defval in _acid_new_cols:
                try:
                    cur.execute(f"ALTER TABLE acidentes_relatorios ADD COLUMN {col} TEXT DEFAULT {defval}")
                except Exception:
                    pass
            conn.autocommit = False
        else:
            try:
                cols_acid = [r[1] for r in cur.execute("PRAGMA table_info(acidentes_relatorios)").fetchall()]
                for col, _ in _acid_new_cols:
                    if col not in cols_acid:
                        cur.execute(f"ALTER TABLE acidentes_relatorios ADD COLUMN {col} TEXT DEFAULT ''")
                conn.commit()
            except Exception:
                pass

        # Tabelas de Acidentes
        if USE_POSTGRES:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS acidentes_relatorios (
                    id SERIAL PRIMARY KEY,
                    funcionario_id INTEGER REFERENCES funcionarios(id),
                    funcionario_nome TEXT DEFAULT '',
                    matricula TEXT DEFAULT '',
                    turno TEXT DEFAULT '',
                    funcao TEXT DEFAULT '',
                    sexo TEXT DEFAULT '',
                    data_nasc TEXT DEFAULT '',
                    data_admissao TEXT DEFAULT '',
                    telefone TEXT DEFAULT '',
                    posto_trabalho TEXT DEFAULT '',
                    chefia_imediata TEXT DEFAULT '',
                    tipo_acidente TEXT DEFAULT '',
                    data_acidente TEXT DEFAULT '',
                    hora_acidente TEXT DEFAULT '',
                    data_preenchimento TEXT DEFAULT '',
                    afastamento TEXT DEFAULT 'nao',
                    num_dias_afastamento INTEGER DEFAULT 0,
                    natureza_lesao TEXT DEFAULT '',
                    agente_causador TEXT DEFAULT '',
                    servico_executava TEXT DEFAULT '',
                    checklist_acidentes_antes TEXT DEFAULT '',
                    checklist_qtd_acidentes TEXT DEFAULT '',
                    checklist_acidente_semelhante TEXT DEFAULT '',
                    checklist_usava_epi TEXT DEFAULT '',
                    checklist_epi_justificativa TEXT DEFAULT '',
                    checklist_treinamento TEXT DEFAULT '',
                    checklist_experiencia TEXT DEFAULT '',
                    checklist_supervisor_presente TEXT DEFAULT '',
                    testemunha1_nome TEXT DEFAULT '',
                    testemunha2_nome TEXT DEFAULT '',
                    supervisor_turno_nome TEXT DEFAULT '',
                    tecnico_seguranca TEXT DEFAULT 'JONATAS DA COSTA XAVIER',
                    analise_acidente TEXT DEFAULT '',
                    descricao_acidente TEXT DEFAULT '',
                    cel_testemunha1 TEXT DEFAULT '',
                    cel_testemunha2 TEXT DEFAULT '',
                    cel_supervisor TEXT DEFAULT '',
                    responsavel_nome TEXT DEFAULT '',
                    responsavel_cpf TEXT DEFAULT '',
                    criado_por TEXT DEFAULT '',
                    criado_em TIMESTAMP DEFAULT NOW(),
                    link_assinatura TEXT DEFAULT '',
                    zapsign_token TEXT DEFAULT ''
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS acidentes_plano_acao (
                    id SERIAL PRIMARY KEY,
                    relatorio_id INTEGER REFERENCES acidentes_relatorios(id) ON DELETE CASCADE,
                    acao TEXT DEFAULT '',
                    responsavel TEXT DEFAULT '',
                    prazo TEXT DEFAULT '',
                    visto TEXT DEFAULT ''
                )
            """)
        else:
            cur.execute("""CREATE TABLE IF NOT EXISTS acidentes_relatorios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                funcionario_id INTEGER REFERENCES funcionarios(id),
                funcionario_nome TEXT DEFAULT '',
                matricula TEXT DEFAULT '',
                turno TEXT DEFAULT '',
                funcao TEXT DEFAULT '',
                sexo TEXT DEFAULT '',
                data_nasc TEXT DEFAULT '',
                data_admissao TEXT DEFAULT '',
                telefone TEXT DEFAULT '',
                posto_trabalho TEXT DEFAULT '',
                chefia_imediata TEXT DEFAULT '',
                tipo_acidente TEXT DEFAULT '',
                data_acidente TEXT DEFAULT '',
                hora_acidente TEXT DEFAULT '',
                data_preenchimento TEXT DEFAULT '',
                afastamento TEXT DEFAULT 'nao',
                num_dias_afastamento INTEGER DEFAULT 0,
                natureza_lesao TEXT DEFAULT '',
                agente_causador TEXT DEFAULT '',
                servico_executava TEXT DEFAULT '',
                checklist_acidentes_antes TEXT DEFAULT '',
                checklist_qtd_acidentes TEXT DEFAULT '',
                checklist_acidente_semelhante TEXT DEFAULT '',
                checklist_usava_epi TEXT DEFAULT '',
                checklist_epi_justificativa TEXT DEFAULT '',
                checklist_treinamento TEXT DEFAULT '',
                checklist_experiencia TEXT DEFAULT '',
                checklist_supervisor_presente TEXT DEFAULT '',
                testemunha1_nome TEXT DEFAULT '',
                testemunha2_nome TEXT DEFAULT '',
                supervisor_turno_nome TEXT DEFAULT '',
                tecnico_seguranca TEXT DEFAULT 'JONATAS DA COSTA XAVIER',
                analise_acidente TEXT DEFAULT '',
                descricao_acidente TEXT DEFAULT '',
                cel_testemunha1 TEXT DEFAULT '',
                cel_testemunha2 TEXT DEFAULT '',
                cel_supervisor TEXT DEFAULT '',
                responsavel_nome TEXT DEFAULT '',
                responsavel_cpf TEXT DEFAULT '',
                criado_por TEXT DEFAULT '',
                criado_em TEXT DEFAULT (datetime('now','localtime')),
                link_assinatura TEXT DEFAULT '',
                zapsign_token TEXT DEFAULT '')""")
            cur.execute("""CREATE TABLE IF NOT EXISTS acidentes_plano_acao (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                relatorio_id INTEGER REFERENCES acidentes_relatorios(id) ON DELETE CASCADE,
                acao TEXT DEFAULT '',
                responsavel TEXT DEFAULT '',
                prazo TEXT DEFAULT '',
                visto TEXT DEFAULT '')""")
        conn.commit()

        print(f"OK Banco criado ({'PostgreSQL' if USE_POSTGRES else 'SQLite'})")
    finally:
        conn.close()


# ── ENGENHEIROS ───────────────────────────────────────────

def listar_engenheiros() -> list:
    conn = conectar()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, nome, crea FROM engenheiros WHERE ativo=1 ORDER BY nome")
        rows = cur.fetchall()
        return [{"id": r[0], "nome": r[1], "crea": r[2]} for r in rows]
    finally:
        conn.close()


def salvar_engenheiro(eid, nome: str, crea: str = '') -> int:
    conn = conectar()
    try:
        cur = conn.cursor()
        if eid:
            cur.execute("UPDATE engenheiros SET nome=%s, crea=%s WHERE id=%s" if USE_POSTGRES
                        else "UPDATE engenheiros SET nome=?, crea=? WHERE id=?",
                        (nome, crea, eid))
            conn.commit()
            return eid
        else:
            if USE_POSTGRES:
                cur.execute("INSERT INTO engenheiros (nome, crea) VALUES (%s, %s) RETURNING id", (nome, crea))
                new_id = cur.fetchone()[0]
            else:
                cur.execute("INSERT INTO engenheiros (nome, crea) VALUES (?, ?)", (nome, crea))
                new_id = cur.lastrowid
            conn.commit()
            return new_id
    finally:
        conn.close()


def deletar_engenheiro(eid: int):
    conn = conectar()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE engenheiros SET ativo=0 WHERE id=%s" if USE_POSTGRES
                    else "UPDATE engenheiros SET ativo=0 WHERE id=?", (eid,))
        conn.commit()
    finally:
        conn.close()


# ── PGR INVENTÁRIO ────────────────────────────────────────

def salvar_pgr_cargo(cargo: str, cbo: str = '', ambiente: str = '', atividades: str = '',
                     riscos: str = '', epis: str = '', epcs: str = ''):
    conn = conectar()
    try:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("""INSERT INTO pgr_inventario (cargo,cbo,ambiente,atividades,riscos,epis,epcs)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (cargo) DO UPDATE SET cbo=EXCLUDED.cbo, ambiente=EXCLUDED.ambiente,
                atividades=EXCLUDED.atividades, riscos=EXCLUDED.riscos, epis=EXCLUDED.epis,
                epcs=EXCLUDED.epcs, atualizado_em=NOW()""",
                (cargo, cbo, ambiente, atividades, riscos, epis, epcs))
        else:
            cur.execute("""INSERT INTO pgr_inventario (cargo,cbo,ambiente,atividades,riscos,epis,epcs)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(cargo) DO UPDATE SET cbo=excluded.cbo, ambiente=excluded.ambiente,
                atividades=excluded.atividades, riscos=excluded.riscos, epis=excluded.epis,
                epcs=excluded.epcs""",
                (cargo, cbo, ambiente, atividades, riscos, epis, epcs))
        conn.commit()
    finally:
        conn.close()

# Aliases: cargo no sistema → cargo de referência no PGR
# "TECNICO SEGURANCA DO TRABALHO" = versão sem acento (como vem de algumas planilhas)
# "TECNICO SEGURANÇA DO TRABALHO" = versão com acento
_TECNICO_SST = "TECNICO SEGURANCA DO TRABALHO"   # canonical sem acento
_CARGO_ALIASES = {
    "TST":                              _TECNICO_SST,
    "TEC SEGURANÇA DO TRABALHO":        _TECNICO_SST,
    "TEC. SEGURANÇA DO TRABALHO":       _TECNICO_SST,
    "TEC SEGURANCA DO TRABALHO":        _TECNICO_SST,
    "TEC. SEGURANCA DO TRABALHO":       _TECNICO_SST,
    "TÉCNICO SEGURANÇA DO TRABALHO":    _TECNICO_SST,
    "TÉCNICO SEGURANCA DO TRABALHO":    _TECNICO_SST,
    "TECNICO SEGURANÇA DO TRABALHO":    _TECNICO_SST,  # com acento → canonical sem acento
}


def _normalizar_cargo(cargo: str) -> str:
    """Remove sufixos de nível/grau para busca normalizada no PGR.
    Ex: 'RASTELEIRO NÍVEL II' → 'RASTELEIRO'
        'AUXILIAR ADMINISTRATIVO NIVEL III' → 'AUXILIAR ADMINISTRATIVO'
        'TST' → 'TECNICO SEGURANÇA DO TRABALHO'
    """
    import re as _re
    s = cargo.upper().strip()

    # Verifica alias direto primeiro
    if s in _CARGO_ALIASES:
        return _CARGO_ALIASES[s]

    # Remove sufixo de nível/grau no final
    s = _re.sub(r'\s+(N[IVX]+|NV\.?\s*[IVX]+|NIVEL\s*[IVX0-9]+|NÍVEL\s*[IVX0-9]+|NIVEL\s+[IVX0-9]+|[IVX]{1,4}|[0-9]+)$', '', s).strip()

    # Verifica alias novamente após remover nível
    if s in _CARGO_ALIASES:
        return _CARGO_ALIASES[s]

    return s


def buscar_pgr_cargo(cargo: str) -> dict:
    """Busca PGR pelo cargo exato; se não achar, tenta pelo cargo normalizado (sem nível)."""
    conn = conectar()
    try:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("SELECT * FROM pgr_inventario WHERE UPPER(cargo)=UPPER(%s)", (cargo,))
        else:
            cur.execute("SELECT * FROM pgr_inventario WHERE UPPER(cargo)=UPPER(?)", (cargo,))
        row = cur.fetchone()

        # Fallback: busca pelo cargo normalizado (sem nível/grau)
        if not row:
            cargo_norm = _normalizar_cargo(cargo)
            if cargo_norm != cargo.upper().strip():
                if USE_POSTGRES:
                    cur.execute("SELECT * FROM pgr_inventario WHERE UPPER(cargo)=UPPER(%s)", (cargo_norm,))
                else:
                    cur.execute("SELECT * FROM pgr_inventario WHERE UPPER(cargo)=UPPER(?)", (cargo_norm,))
                row = cur.fetchone()

        # Fallback 2: busca pelo CBO — se cargo tem CBO cadastrado, pega qualquer PGR com mesmo CBO
        if not row:
            cur2 = conn.cursor()
            if USE_POSTGRES:
                cur2.execute("SELECT cbo_codigo FROM cargo_cbo WHERE UPPER(cargo)=UPPER(%s)", (cargo,))
            else:
                cur2.execute("SELECT cbo_codigo FROM cargo_cbo WHERE UPPER(cargo)=UPPER(?)", (cargo,))
            cbo_row = cur2.fetchone()
            if cbo_row and cbo_row[0]:
                cbo_codigo = cbo_row[0]
                if USE_POSTGRES:
                    cur.execute("SELECT * FROM pgr_inventario WHERE cbo=%s LIMIT 1", (cbo_codigo,))
                else:
                    cur.execute("SELECT * FROM pgr_inventario WHERE cbo=? LIMIT 1", (cbo_codigo,))
                row = cur.fetchone()

        if not row:
            return {}
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    finally:
        conn.close()

def listar_pgr() -> list:
    conn = conectar()
    try:
        cur = conn.cursor()
        cur.execute("SELECT cargo, cbo, ambiente, riscos, epis FROM pgr_inventario ORDER BY cargo")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()

# ── CATÁLOGO DE EPIs ──────────────────────────────────────

def salvar_epi_catalogo(descricao: str, ca: str = '', quantidade_padrao: int = 1) -> int:
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
            cur.execute("SELECT id FROM catalogo_epis WHERE descricao=%s", (descricao,))
            row = cur.fetchone()
            if row:
                cur.execute("UPDATE catalogo_epis SET ca=%s, quantidade_padrao=%s WHERE descricao=%s",
                            (ca, quantidade_padrao, descricao))
                eid = row["id"]
            else:
                cur.execute("INSERT INTO catalogo_epis (descricao,ca,quantidade_padrao) VALUES (%s,%s,%s) RETURNING id",
                            (descricao, ca, quantidade_padrao))
                eid = cur.fetchone()["id"]
        else:
            cur = conn.cursor()
            cur.execute("SELECT id FROM catalogo_epis WHERE descricao=?", (descricao,))
            row = cur.fetchone()
            if row:
                cur.execute("UPDATE catalogo_epis SET ca=?, quantidade_padrao=? WHERE descricao=?",
                            (ca, quantidade_padrao, descricao))
                eid = row[0]
            else:
                cur.execute("INSERT INTO catalogo_epis (descricao,ca,quantidade_padrao) VALUES (?,?,?)",
                            (descricao, ca, quantidade_padrao))
                eid = cur.lastrowid
        conn.commit()
        return eid
    finally:
        conn.close()


def listar_catalogo_epis() -> list:
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
        else:
            cur = conn.cursor()
        cur.execute("SELECT id, descricao, ca, quantidade_padrao FROM catalogo_epis WHERE ativo=1 ORDER BY descricao")
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def deletar_epi_catalogo(epi_id: int):
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("DELETE FROM catalogo_epis WHERE id=%s", (epi_id,))
        else:
            cur = conn.cursor()
            cur.execute("DELETE FROM catalogo_epis WHERE id=?", (epi_id,))
        conn.commit()
    finally:
        conn.close()


def salvar_cargo_epis(cargo: str, epis: list):
    """epis = lista de dicts com {epi_id, quantidade}"""
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("DELETE FROM cargo_epis WHERE cargo=%s", (cargo,))
            for e in epis:
                cur.execute("INSERT INTO cargo_epis (cargo,epi_id,quantidade) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                            (cargo, e["epi_id"], e.get("quantidade", 1)))
        else:
            cur = conn.cursor()
            cur.execute("DELETE FROM cargo_epis WHERE cargo=?", (cargo,))
            for e in epis:
                cur.execute("INSERT OR IGNORE INTO cargo_epis (cargo,epi_id,quantidade) VALUES (?,?,?)",
                            (cargo, e["epi_id"], e.get("quantidade", 1)))
        conn.commit()
    finally:
        conn.close()


def listar_epis_do_cargo(cargo: str) -> list:
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
            cur.execute("""SELECT ce.id, ce.epi_id, c.descricao, c.ca, ce.quantidade
                FROM cargo_epis ce JOIN catalogo_epis c ON c.id=ce.epi_id
                WHERE ce.cargo=%s ORDER BY c.descricao""", (cargo,))
        else:
            cur = conn.cursor()
            cur.execute("""SELECT ce.id, ce.epi_id, c.descricao, c.ca, ce.quantidade
                FROM cargo_epis ce JOIN catalogo_epis c ON c.id=ce.epi_id
                WHERE ce.cargo=? ORDER BY c.descricao""", (cargo,))
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def salvar_cargo_cbo(cargo: str, cbo_codigo: str, cbo_titulo: str, cbo_descricao: str):
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("SELECT id FROM cargo_cbo WHERE cargo=%s", (cargo,))
            existe = cur.fetchone()
            if existe:
                cur.execute("""UPDATE cargo_cbo SET cbo_codigo=%s, cbo_titulo=%s, cbo_descricao=%s,
                    atualizado_em=NOW() WHERE cargo=%s""", (cbo_codigo, cbo_titulo, cbo_descricao, cargo))
            else:
                cur.execute("""INSERT INTO cargo_cbo (cargo,cbo_codigo,cbo_titulo,cbo_descricao)
                    VALUES (%s,%s,%s,%s)""", (cargo, cbo_codigo, cbo_titulo, cbo_descricao))
        else:
            cur = conn.cursor()
            cur.execute("SELECT id FROM cargo_cbo WHERE cargo=?", (cargo,))
            existe = cur.fetchone()
            if existe:
                cur.execute("""UPDATE cargo_cbo SET cbo_codigo=?, cbo_titulo=?, cbo_descricao=?,
                    atualizado_em=datetime('now','localtime') WHERE cargo=?""",
                    (cbo_codigo, cbo_titulo, cbo_descricao, cargo))
            else:
                cur.execute("""INSERT INTO cargo_cbo (cargo,cbo_codigo,cbo_titulo,cbo_descricao)
                    VALUES (?,?,?,?)""", (cargo, cbo_codigo, cbo_titulo, cbo_descricao))
        conn.commit()
    finally:
        conn.close()


def buscar_cargo_cbo(cargo: str) -> dict | None:
    """Busca CBO do cargo; se não achar exato, tenta cargo normalizado."""
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
            cur.execute("SELECT * FROM cargo_cbo WHERE UPPER(cargo)=UPPER(%s)", (cargo,))
        else:
            cur = conn.cursor()
            cur.execute("SELECT * FROM cargo_cbo WHERE UPPER(cargo)=UPPER(?)", (cargo,))
        row = cur.fetchone()
        if not row:
            cargo_norm = _normalizar_cargo(cargo)
            if cargo_norm != cargo.upper().strip():
                if USE_POSTGRES:
                    cur.execute("SELECT * FROM cargo_cbo WHERE UPPER(cargo)=UPPER(%s)", (cargo_norm,))
                else:
                    cur.execute("SELECT * FROM cargo_cbo WHERE UPPER(cargo)=UPPER(?)", (cargo_norm,))
                row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def listar_cargos_cbo() -> list:
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
            cur.execute("SELECT cargo, cbo_codigo, cbo_titulo, cbo_descricao FROM cargo_cbo ORDER BY cargo")
            return [dict(r) for r in cur.fetchall()]
        else:
            cur = conn.cursor()
            cur.execute("SELECT cargo, cbo_codigo, cbo_titulo, cbo_descricao FROM cargo_cbo ORDER BY cargo")
            cols = ['cargo','cbo_codigo','cbo_titulo','cbo_descricao']
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def propagar_cbo_variantes():
    """Propaga CBO de cargos base para variantes (NIVEL I/II/III, aliases) que ainda não têm CBO."""
    todos = buscar_cargos()
    cargos_com_cbo = listar_cargos_cbo()
    # Só considera "configurado" se realmente tem cbo_codigo ou cbo_descricao
    cbo_map = {r["cargo"].upper(): r for r in cargos_com_cbo
               if r.get("cbo_codigo") or r.get("cbo_descricao")}
    propagados = []
    for cargo in todos:
        cargo_up = cargo.upper().strip()
        if cargo_up in cbo_map:
            continue  # já tem CBO configurado
        cargo_norm = _normalizar_cargo(cargo_up)
        if cargo_norm in cbo_map:
            src = cbo_map[cargo_norm]
            salvar_cargo_cbo(cargo, src["cbo_codigo"], src["cbo_titulo"], src["cbo_descricao"])
            propagados.append(cargo)
    return propagados


def buscar_funcionarios(termo="", apenas_ativos=True):
    conn = conectar()
    try:
        filtro = "AND f.ativo=1" if apenas_ativos else ""
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
            cur.execute(f"""
                SELECT f.*, COALESCE(c.cbo_codigo,'') as cbo
                FROM funcionarios f
                LEFT JOIN cargo_cbo c ON UPPER(c.cargo)=UPPER(f.cargo)
                WHERE (f.nome ILIKE %s OR f.cpf ILIKE %s OR f.cargo ILIKE %s OR f.lotacao ILIKE %s) {filtro}
                ORDER BY f.nome""", (f"%{termo}%",)*4)
        else:
            cur = conn.cursor()
            cur.execute(f"""
                SELECT f.*, COALESCE(c.cbo_codigo,'') as cbo
                FROM funcionarios f
                LEFT JOIN cargo_cbo c ON UPPER(c.cargo)=UPPER(f.cargo)
                WHERE (f.nome LIKE ? OR f.cpf LIKE ? OR f.cargo LIKE ? OR f.lotacao LIKE ?) {filtro}
                ORDER BY f.nome""", (f"%{termo}%",)*4)
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def importar_funcionarios(lista):
    conn = conectar()
    inseridos = atualizados = 0
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
        else:
            cur = conn.cursor()

        for f in lista:
            cpf = f.get("cpf", "").strip()
            if not cpf:
                continue

            if USE_POSTGRES:
                cur.execute("SELECT id FROM funcionarios WHERE cpf=%s", (cpf,))
            else:
                cur.execute("SELECT id FROM funcionarios WHERE cpf=?", (cpf,))

            existe = cur.fetchone()

            if existe:
                if USE_POSTGRES:
                    cur.execute("""UPDATE funcionarios SET nome=%s,cargo=%s,lotacao=%s,
                        admissao=%s,celular=%s,email=%s,ativo=1 WHERE cpf=%s""",
                        (f.get("nome",""),f.get("cargo",""),f.get("lotacao",""),
                         f.get("admissao",""),f.get("celular",""),f.get("email",""),cpf))
                else:
                    cur.execute("""UPDATE funcionarios SET nome=?,cargo=?,lotacao=?,
                        admissao=?,celular=?,email=?,ativo=1 WHERE cpf=?""",
                        (f.get("nome",""),f.get("cargo",""),f.get("lotacao",""),
                         f.get("admissao",""),f.get("celular",""),f.get("email",""),cpf))
                atualizados += 1
            else:
                if USE_POSTGRES:
                    cur.execute("""INSERT INTO funcionarios (nome,cpf,matricula,cargo,lotacao,admissao,celular,email)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (f.get("nome",""),cpf,f.get("matricula",""),f.get("cargo",""),
                         f.get("lotacao",""),f.get("admissao",""),f.get("celular",""),f.get("email","")))
                else:
                    cur.execute("""INSERT INTO funcionarios (nome,cpf,matricula,cargo,lotacao,admissao,celular,email)
                        VALUES (?,?,?,?,?,?,?,?)""",
                        (f.get("nome",""),cpf,f.get("matricula",""),f.get("cargo",""),
                         f.get("lotacao",""),f.get("admissao",""),f.get("celular",""),f.get("email","")))
                inseridos += 1

        conn.commit()
    finally:
        conn.close()
    return inseridos, atualizados


def salvar_funcionario(dados):
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
            # Verifica CPF duplicado em outro registro antes de UPDATE
            fid_check = dados.get("id")
            if fid_check:
                cur.execute("SELECT id FROM funcionarios WHERE cpf=%s AND id<>%s", (dados["cpf"], fid_check))
                if cur.fetchone():
                    raise ValueError(f"CPF {dados['cpf']} já está cadastrado para outro funcionário.")
        else:
            cur = conn.cursor()
            fid_check = dados.get("id")
            if fid_check:
                cur.execute("SELECT id FROM funcionarios WHERE cpf=? AND id<>?", (dados["cpf"], fid_check))
                if cur.fetchone():
                    raise ValueError(f"CPF {dados['cpf']} já está cadastrado para outro funcionário.")

        fid = dados.get("id")
        if fid:
            if USE_POSTGRES:
                cur.execute("""UPDATE funcionarios SET nome=%s,cpf=%s,matricula=%s,cargo=%s,
                    lotacao=%s,admissao=%s,celular=%s,email=%s WHERE id=%s""",
                    (dados["nome"],dados["cpf"],dados.get("matricula",""),dados["cargo"],
                     dados.get("lotacao",""),dados.get("admissao",""),dados.get("celular",""),
                     dados.get("email",""),fid))
            else:
                cur.execute("""UPDATE funcionarios SET nome=?,cpf=?,matricula=?,cargo=?,
                    lotacao=?,admissao=?,celular=?,email=? WHERE id=?""",
                    (dados["nome"],dados["cpf"],dados.get("matricula",""),dados["cargo"],
                     dados.get("lotacao",""),dados.get("admissao",""),dados.get("celular",""),
                     dados.get("email",""),fid))
        else:
            if USE_POSTGRES:
                cur.execute("""INSERT INTO funcionarios (nome,cpf,matricula,cargo,lotacao,admissao,celular,email)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (dados["nome"],dados["cpf"],dados.get("matricula",""),dados["cargo"],
                     dados.get("lotacao",""),dados.get("admissao",""),dados.get("celular",""),dados.get("email","")))
                fid = cur.fetchone()["id"]
            else:
                cur.execute("""INSERT INTO funcionarios (nome,cpf,matricula,cargo,lotacao,admissao,celular,email)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (dados["nome"],dados["cpf"],dados.get("matricula",""),dados["cargo"],
                     dados.get("lotacao",""),dados.get("admissao",""),dados.get("celular",""),dados.get("email","")))
                fid = cur.lastrowid

        conn.commit()
        return fid
    finally:
        conn.close()


def docs_do_cargo(cargo):
    from config import KIT_PADRAO
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
            cur.execute("SELECT doc_id FROM funcao_documentos WHERE cargo=%s ORDER BY doc_id", (cargo,))
        else:
            cur = conn.cursor()
            cur.execute("SELECT doc_id FROM funcao_documentos WHERE cargo=? ORDER BY doc_id", (cargo,))
        rows = cur.fetchall()
        docs_cargo = [dict(r)["doc_id"] for r in rows]
        # Sempre inclui o Kit Padrão, sem duplicar
        todos = KIT_PADRAO + [d for d in docs_cargo if d not in KIT_PADRAO]
        return todos
    finally:
        conn.close()


def salvar_docs_cargo(cargo, doc_ids):
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("DELETE FROM funcao_documentos WHERE cargo=%s", (cargo,))
            for doc_id in doc_ids:
                cur.execute("INSERT INTO funcao_documentos (cargo,doc_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (cargo, doc_id))
        else:
            cur = conn.cursor()
            cur.execute("DELETE FROM funcao_documentos WHERE cargo=?", (cargo,))
            for doc_id in doc_ids:
                cur.execute("INSERT OR IGNORE INTO funcao_documentos (cargo,doc_id) VALUES (?,?)", (cargo, doc_id))
        conn.commit()
    finally:
        conn.close()


def buscar_cargos():
    """Cargos de funcionários cadastrados — usado na lista da OS."""
    conn = conectar()
    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT cargo FROM funcionarios WHERE cargo IS NOT NULL AND cargo != '' ORDER BY cargo")
        rows = cur.fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def buscar_cargos_configurados():
    """Cargos com EPIs ou OS configurados — usado nos dropdowns de Kit e seleção."""
    conn = conectar()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT cargo FROM (
                SELECT cargo FROM funcionarios WHERE cargo IS NOT NULL AND cargo != ''
                UNION
                SELECT DISTINCT cargo FROM cargo_epis WHERE cargo IS NOT NULL AND cargo != ''
            ) t ORDER BY cargo
        """)
        rows = cur.fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def criar_lote(descricao=""):
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
            cur.execute("INSERT INTO lotes (descricao) VALUES (%s) RETURNING id", (descricao,))
            lote_id = cur.fetchone()["id"]
        else:
            cur = conn.cursor()
            cur.execute("INSERT INTO lotes (descricao) VALUES (?)", (descricao,))
            lote_id = cur.lastrowid
        conn.commit()
        return lote_id
    finally:
        conn.close()


def adicionar_ao_lote(lote_id, funcionario_id):
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("INSERT INTO lote_funcionarios (lote_id,funcionario_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (lote_id, funcionario_id))
        else:
            cur = conn.cursor()
            cur.execute("INSERT OR IGNORE INTO lote_funcionarios (lote_id,funcionario_id) VALUES (?,?)", (lote_id, funcionario_id))
        conn.commit()
    finally:
        conn.close()


def registrar_envio(dados):
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
            cur.execute("""INSERT INTO envios (funcionario_id,doc_id,doc_nome,pdf_path,
                autentique_id,link_assinatura,status,enviado_em)
                VALUES (%s,%s,%s,%s,%s,%s,%s,NOW()) RETURNING id""",
                (dados["funcionario_id"],dados["doc_id"],dados.get("doc_nome",""),
                 dados.get("pdf_path",""),dados.get("autentique_id",""),
                 dados.get("link_assinatura",""),dados.get("status","enviado")))
            eid = cur.fetchone()["id"]
        else:
            cur = conn.cursor()
            cur.execute("""INSERT INTO envios (funcionario_id,doc_id,doc_nome,pdf_path,
                autentique_id,link_assinatura,status,enviado_em)
                VALUES (?,?,?,?,?,?,?,datetime('now','localtime'))""",
                (dados["funcionario_id"],dados["doc_id"],dados.get("doc_nome",""),
                 dados.get("pdf_path",""),dados.get("autentique_id",""),
                 dados.get("link_assinatura",""),dados.get("status","enviado")))
            eid = cur.lastrowid
        conn.commit()
        return eid
    finally:
        conn.close()


def listar_envios(funcionario_id: int = None, status: str = None, limite: int = 100):
    """Lista o histórico de documentos enviados para assinatura."""
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
            filtros = []
            params  = []
            if funcionario_id:
                filtros.append("e.funcionario_id = %s")
                params.append(funcionario_id)
            if status:
                filtros.append("e.status = %s")
                params.append(status)
            where = ("WHERE " + " AND ".join(filtros)) if filtros else ""
            params.append(limite)
            cur.execute(f"""
                SELECT e.id, e.doc_id, e.doc_nome,
                       f.nome AS funcionario, f.cargo, f.celular,
                       e.status, e.link_assinatura,
                       e.autentique_id AS zapsign_token,
                       e.enviado_em, e.assinado_em
                FROM envios e
                LEFT JOIN funcionarios f ON f.id = e.funcionario_id
                {where}
                ORDER BY e.enviado_em DESC
                LIMIT %s
            """, params)
        else:
            cur = conn.cursor()
            filtros = []
            params  = []
            if funcionario_id:
                filtros.append("e.funcionario_id = ?")
                params.append(funcionario_id)
            if status:
                filtros.append("e.status = ?")
                params.append(status)
            where = ("WHERE " + " AND ".join(filtros)) if filtros else ""
            params.append(limite)
            cur.execute(f"""
                SELECT e.id, e.doc_id, e.doc_nome,
                       f.nome AS funcionario, f.cargo, f.celular,
                       e.status, e.link_assinatura,
                       e.autentique_id AS zapsign_token,
                       e.enviado_em, e.assinado_em
                FROM envios e
                LEFT JOIN funcionarios f ON f.id = e.funcionario_id
                {where}
                ORDER BY e.enviado_em DESC
                LIMIT ?
            """, params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def buscar_envio_por_id(envio_id: int):
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
            cur.execute("SELECT * FROM envios WHERE id=%s", (envio_id,))
        else:
            cur = conn.cursor()
            cur.execute("SELECT * FROM envios WHERE id=?", (envio_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def atualizar_status_envio(envio_id: int, status: str, assinado_em=None):
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor()
            if assinado_em and status == "signed":
                cur.execute("UPDATE envios SET status=%s, assinado_em=%s WHERE id=%s",
                            (status, assinado_em, envio_id))
            else:
                cur.execute("UPDATE envios SET status=%s WHERE id=%s", (status, envio_id))
        else:
            cur = conn.cursor()
            if assinado_em and status == "signed":
                cur.execute("UPDATE envios SET status=?, assinado_em=? WHERE id=?",
                            (status, assinado_em, envio_id))
            else:
                cur.execute("UPDATE envios SET status=? WHERE id=?", (status, envio_id))
        conn.commit()
    finally:
        conn.close()


def listar_lotes():
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
        else:
            cur = conn.cursor()
        cur.execute("""SELECT l.id, l.descricao, l.criado_em, l.status,
            COUNT(lf.id) as total_func,
            SUM(CASE WHEN lf.status='enviado' THEN 1 ELSE 0 END) as enviados
            FROM lotes l LEFT JOIN lote_funcionarios lf ON lf.lote_id=l.id
            GROUP BY l.id, l.descricao, l.criado_em, l.status
            ORDER BY l.criado_em DESC""")
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def salvar_modelo(doc_id: str, nome: str, conteudo_bytes: bytes, cargo: str = None):
    """Salva ou atualiza um modelo .docx no banco."""
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("SELECT pk FROM modelos WHERE id=%s AND cargo IS NOT DISTINCT FROM %s", (doc_id, cargo))
            existe = cur.fetchone()
            if existe:
                cur.execute("UPDATE modelos SET nome=%s, conteudo=%s WHERE id=%s AND cargo IS NOT DISTINCT FROM %s",
                            (nome, conteudo_bytes, doc_id, cargo))
            else:
                cur.execute("INSERT INTO modelos (id, nome, conteudo, cargo) VALUES (%s,%s,%s,%s)",
                            (doc_id, nome, conteudo_bytes, cargo))
        else:
            cur = conn.cursor()
            if cargo is None:
                cur.execute("SELECT pk FROM modelos WHERE id=? AND cargo IS NULL", (doc_id,))
            else:
                cur.execute("SELECT pk FROM modelos WHERE id=? AND cargo=?", (doc_id, cargo))
            existe = cur.fetchone()
            if existe:
                if cargo is None:
                    cur.execute("UPDATE modelos SET nome=?, conteudo=? WHERE id=? AND cargo IS NULL",
                                (nome, conteudo_bytes, doc_id))
                else:
                    cur.execute("UPDATE modelos SET nome=?, conteudo=? WHERE id=? AND cargo=?",
                                (nome, conteudo_bytes, doc_id, cargo))
            else:
                cur.execute("INSERT INTO modelos (id, nome, conteudo, cargo) VALUES (?,?,?,?)",
                            (doc_id, nome, conteudo_bytes, cargo))
        conn.commit()
    finally:
        conn.close()


def buscar_modelo(doc_id: str, cargo: str = None) -> bytes | None:
    """
    Retorna bytes do modelo .docx.
    Tenta cargo específico primeiro, depois fallback para modelo geral (cargo IS NULL).
    """
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor()
            # Tenta específico de cargo primeiro
            if cargo:
                cur.execute("SELECT conteudo FROM modelos WHERE id=%s AND cargo=%s", (doc_id, cargo))
                row = cur.fetchone()
                if row and row[0]:
                    return bytes(row[0])
            # Fallback geral
            cur.execute("SELECT conteudo FROM modelos WHERE id=%s AND cargo IS NULL", (doc_id,))
            row = cur.fetchone()
            return bytes(row[0]) if row and row[0] else None
        else:
            cur = conn.cursor()
            if cargo:
                cur.execute("SELECT conteudo FROM modelos WHERE id=? AND cargo=?", (doc_id, cargo))
                row = cur.fetchone()
                if row and row[0]:
                    return bytes(row[0])
            cur.execute("SELECT conteudo FROM modelos WHERE id=? AND cargo IS NULL", (doc_id,))
            row = cur.fetchone()
            return bytes(row[0]) if row and row[0] else None
    finally:
        conn.close()


def listar_modelos() -> list:
    """Retorna lista de modelos com id, nome, cargo e tem_conteudo (bool)."""
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(_psycopg2_extras.RealDictCursor) if _psycopg2_extras else conn.cursor()
            cur.execute("SELECT id, nome, cargo, (conteudo IS NOT NULL) AS tem_conteudo FROM modelos ORDER BY id, cargo")
        else:
            cur = conn.cursor()
            cur.execute("SELECT id, nome, cargo, (conteudo IS NOT NULL) AS tem_conteudo FROM modelos ORDER BY id, cargo")
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def purgar_modelos_padrao() -> list:
    """
    Deleta TODOS os registros de modelos (cargo IS NULL) para os IDs do Kit Padrão
    e documentos padrão (01-14). Preserva modelos cargo-específicos (OS e EPI por cargo).
    Retorna lista de ids deletados.
    """
    from config import DOCUMENTOS
    ids_padrao = [d["id"] for d in DOCUMENTOS]
    conn = conectar()
    deletados = []
    try:
        cur = conn.cursor()
        for doc_id in ids_padrao:
            if USE_POSTGRES:
                cur.execute("DELETE FROM modelos WHERE id=%s AND cargo IS NULL", (doc_id,))
            else:
                cur.execute("DELETE FROM modelos WHERE id=? AND cargo IS NULL", (doc_id,))
            if cur.rowcount:
                deletados.append(doc_id)
        conn.commit()
    finally:
        conn.close()
    return deletados


def deletar_modelo(doc_id: str, cargo: str = None):
    """Remove um modelo do banco."""
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("DELETE FROM modelos WHERE id=%s AND cargo IS NOT DISTINCT FROM %s", (doc_id, cargo))
        else:
            cur = conn.cursor()
            if cargo is None:
                cur.execute("DELETE FROM modelos WHERE id=? AND cargo IS NULL", (doc_id,))
            else:
                cur.execute("DELETE FROM modelos WHERE id=? AND cargo=?", (doc_id, cargo))
        conn.commit()
    finally:
        conn.close()


# ── DOCUMENTOS EXTRAS ─────────────────────────────────────

def salvar_documento_extra(doc_id: str, nome: str) -> bool:
    conn = conectar()
    try:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute(
                "INSERT INTO documentos_extras(id, nome) VALUES(%s,%s) ON CONFLICT(id) DO UPDATE SET nome=EXCLUDED.nome",
                (doc_id, nome)
            )
        else:
            cur.execute(
                "INSERT OR REPLACE INTO documentos_extras(id, nome) VALUES(?,?)",
                (doc_id, nome)
            )
        conn.commit()
        return True
    finally:
        conn.close()


def listar_documentos_extras() -> list:
    conn = conectar()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, nome FROM documentos_extras ORDER BY nome")
        rows = cur.fetchall()
        return [{"id": r[0], "nome": r[1]} for r in rows]
    finally:
        conn.close()


def deletar_documento_extra(doc_id: str):
    conn = conectar()
    try:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("DELETE FROM documentos_extras WHERE id=%s", (doc_id,))
            cur.execute("DELETE FROM modelos WHERE id=%s AND cargo IS NULL", (doc_id,))
        else:
            cur.execute("DELETE FROM documentos_extras WHERE id=?", (doc_id,))
            cur.execute("DELETE FROM modelos WHERE id=? AND cargo IS NULL", (doc_id,))
        conn.commit()
    finally:
        conn.close()


def limpar_todos_extras():
    """Remove todos os documentos extras e seus modelos do banco."""
    conn = conectar()
    try:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("SELECT id FROM documentos_extras")
            ids = [r[0] for r in cur.fetchall()]
            cur.execute("DELETE FROM documentos_extras")
            for doc_id in ids:
                cur.execute("DELETE FROM modelos WHERE id=%s AND cargo IS NULL", (doc_id,))
        else:
            cur.execute("SELECT id FROM documentos_extras")
            ids = [r[0] for r in cur.fetchall()]
            cur.execute("DELETE FROM documentos_extras")
            for doc_id in ids:
                cur.execute("DELETE FROM modelos WHERE id=? AND cargo IS NULL", (doc_id,))
        conn.commit()
        return len(ids)
    finally:
        conn.close()


# ── USUÁRIOS ──────────────────────────────────────────────

def criar_usuario(nome: str, login: str, senha_hash: str, perfil: str = "usuario", permissoes: list = None) -> int:
    import json
    perms = json.dumps(permissoes or [])
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
            cur.execute(
                "INSERT INTO usuarios (nome,login,senha_hash,perfil,permissoes) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                (nome, login, senha_hash, perfil, perms)
            )
            uid = cur.fetchone()["id"]
        else:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO usuarios (nome,login,senha_hash,perfil,permissoes) VALUES (?,?,?,?,?)",
                (nome, login, senha_hash, perfil, perms)
            )
            uid = cur.lastrowid
        conn.commit()
        return uid
    finally:
        conn.close()


def buscar_usuario_por_login(login: str) -> dict | None:
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
            cur.execute("SELECT * FROM usuarios WHERE login=%s AND ativo=1", (login,))
        else:
            cur = conn.cursor()
            cur.execute("SELECT * FROM usuarios WHERE login=? AND ativo=1", (login,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def buscar_usuario_por_id(uid: int) -> dict | None:
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
            cur.execute("SELECT * FROM usuarios WHERE id=%s", (uid,))
        else:
            cur = conn.cursor()
            cur.execute("SELECT * FROM usuarios WHERE id=?", (uid,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def listar_usuarios() -> list:
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
            cur.execute("SELECT id,nome,login,perfil,permissoes,ativo,criado_em FROM usuarios ORDER BY nome")
        else:
            cur = conn.cursor()
            cur.execute("SELECT id,nome,login,perfil,permissoes,ativo,criado_em FROM usuarios ORDER BY nome")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def atualizar_usuario(uid: int, dados: dict):
    import json
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor()
            if "senha_hash" in dados:
                cur.execute(
                    "UPDATE usuarios SET nome=%s,login=%s,senha_hash=%s,perfil=%s,permissoes=%s,ativo=%s WHERE id=%s",
                    (dados["nome"], dados["login"], dados["senha_hash"],
                     dados["perfil"], json.dumps(dados.get("permissoes", [])), dados.get("ativo", 1), uid)
                )
            else:
                cur.execute(
                    "UPDATE usuarios SET nome=%s,login=%s,perfil=%s,permissoes=%s,ativo=%s WHERE id=%s",
                    (dados["nome"], dados["login"], dados["perfil"],
                     json.dumps(dados.get("permissoes", [])), dados.get("ativo", 1), uid)
                )
        else:
            cur = conn.cursor()
            if "senha_hash" in dados:
                cur.execute(
                    "UPDATE usuarios SET nome=?,login=?,senha_hash=?,perfil=?,permissoes=?,ativo=? WHERE id=?",
                    (dados["nome"], dados["login"], dados["senha_hash"],
                     dados["perfil"], json.dumps(dados.get("permissoes", [])), dados.get("ativo", 1), uid)
                )
            else:
                cur.execute(
                    "UPDATE usuarios SET nome=?,login=?,perfil=?,permissoes=?,ativo=? WHERE id=?",
                    (dados["nome"], dados["login"], dados["perfil"],
                     json.dumps(dados.get("permissoes", [])), dados.get("ativo", 1), uid)
                )
        conn.commit()
    finally:
        conn.close()


def deletar_usuario(uid: int):
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("DELETE FROM usuarios WHERE id=%s", (uid,))
        else:
            cur = conn.cursor()
            cur.execute("DELETE FROM usuarios WHERE id=?", (uid,))
        conn.commit()
    finally:
        conn.close()


def contar_admins() -> int:
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM usuarios WHERE perfil='admin' AND ativo=1")
        else:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM usuarios WHERE perfil='admin' AND ativo=1")
        row = cur.fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


# ── ALOJAMENTOS ───────────────────────────────────────────

def salvar_vistoria_alojamento(dados: dict, usuario: str = '') -> int:
    campos = ['frente_servico','contrato','localizacao','data_vistoria','num_trabalhadores',
              'responsavel','cargo_responsavel','encarregado','cel_encarregado','resultado','prazo_regularizacao',
              'observacao_geral','assinatura_responsavel','assinatura_encarregado']
    vals = [dados.get(c, '') for c in campos]
    vid = dados.get('id')
    if vid:
        # Tenta UPDATE com todos os campos; se alguma coluna não existir, retenta sem ela
        def _try_update(cols, values):
            sets = ', '.join(f'{c}=?' for c in cols)
            executar(f'UPDATE alojamento_vistorias SET {sets} WHERE id=?', list(values) + [vid], commit=True)

        try:
            _try_update(campos, vals)
        except Exception as e:
            err = str(e).lower()
            # Remove colunas inexistentes e tenta novamente
            campos_ok = [c for c in campos if c not in ('cel_encarregado',)] if 'cel_encarregado' in err or 'column' in err else campos
            vals_ok = [dados.get(c, '') for c in campos_ok]
            _try_update(campos_ok, vals_ok)
        return int(vid)
    else:
        cols = ', '.join(campos) + ', criado_por'
        placeholders = ', '.join(['?'] * (len(campos) + 1))
        if USE_POSTGRES:
            q = f'INSERT INTO alojamento_vistorias ({cols}) VALUES ({placeholders}) RETURNING id'
            row = executar(q, vals + [usuario], fetchone=True, commit=True)
            return row['id']
        else:
            executar(f'INSERT INTO alojamento_vistorias ({cols}) VALUES ({placeholders})',
                     vals + [usuario], commit=True)
            conn = conectar()
            try:
                cur = conn.cursor()
                cur.execute('SELECT last_insert_rowid()')
                return cur.fetchone()[0]
            finally:
                conn.close()


def salvar_itens_vistoria(vistoria_id: int, itens: list):
    executar('DELETE FROM alojamento_itens WHERE vistoria_id=?', (vistoria_id,), commit=True)
    for item in itens:
        executar('INSERT INTO alojamento_itens (vistoria_id,bloco,item_num,descricao,status,observacao) VALUES (?,?,?,?,?,?)',
                 (vistoria_id, item.get('bloco'), item.get('item_num'), item.get('descricao'),
                  item.get('status','na'), item.get('observacao','')), commit=True)


def salvar_fotos_vistoria(vistoria_id: int, fotos: list):
    executar('DELETE FROM alojamento_fotos WHERE vistoria_id=?', (vistoria_id,), commit=True)
    for f in fotos:
        executar('INSERT INTO alojamento_fotos (vistoria_id,nome_arquivo,dados_base64) VALUES (?,?,?)',
                 (vistoria_id, f.get('nome_arquivo','foto.jpg'), f.get('dados_base64','')), commit=True)


def salvar_plano_acao_vistoria(vistoria_id: int, acoes: list):
    executar('DELETE FROM alojamento_plano_acao WHERE vistoria_id=?', (vistoria_id,), commit=True)
    for a in acoes:
        executar('INSERT INTO alojamento_plano_acao (vistoria_id,num_nc,descricao,responsavel,prazo,status_acao) VALUES (?,?,?,?,?,?)',
                 (vistoria_id, a.get('num_nc',''), a.get('descricao',''), a.get('responsavel',''),
                  a.get('prazo',''), a.get('status_acao','pendente')), commit=True)


def listar_vistorias_alojamento() -> list:
    return executar(
        'SELECT id,frente_servico,contrato,localizacao,data_vistoria,responsavel,resultado,criado_em FROM alojamento_vistorias ORDER BY criado_em DESC',
        fetchall=True) or []


def buscar_vistoria_alojamento(vistoria_id: int) -> dict:
    v = executar('SELECT * FROM alojamento_vistorias WHERE id=?', (vistoria_id,), fetchone=True)
    if not v:
        return None
    v['itens'] = executar('SELECT * FROM alojamento_itens WHERE vistoria_id=? ORDER BY bloco,item_num',
                          (vistoria_id,), fetchall=True) or []
    v['fotos'] = executar('SELECT id,nome_arquivo,dados_base64 FROM alojamento_fotos WHERE vistoria_id=?',
                          (vistoria_id,), fetchall=True) or []
    v['plano_acao'] = executar('SELECT * FROM alojamento_plano_acao WHERE vistoria_id=?',
                               (vistoria_id,), fetchall=True) or []
    return v


def deletar_vistoria_alojamento(vistoria_id: int):
    executar('DELETE FROM alojamento_plano_acao WHERE vistoria_id=?', (vistoria_id,), commit=True)
    executar('DELETE FROM alojamento_fotos WHERE vistoria_id=?', (vistoria_id,), commit=True)
    executar('DELETE FROM alojamento_itens WHERE vistoria_id=?', (vistoria_id,), commit=True)
    executar('DELETE FROM alojamento_vistorias WHERE id=?', (vistoria_id,), commit=True)


def salvar_link_vistoria(vistoria_id: int, link: str, token: str):
    executar('UPDATE alojamento_vistorias SET link_assinatura=?, zapsign_token=? WHERE id=?',
             (link, token, vistoria_id), commit=True)


# ── ACIDENTES ─────────────────────────────────────────────

def salvar_relatorio_acidente(dados: dict, usuario: str = '') -> int:
    campos = [
        'funcionario_id','funcionario_nome','matricula','turno','funcao','sexo',
        'data_nasc','data_admissao','telefone','posto_trabalho','chefia_imediata',
        'tipo_acidente','data_acidente','hora_acidente','data_preenchimento',
        'afastamento','num_dias_afastamento','natureza_lesao','agente_causador',
        'servico_executava','checklist_acidentes_antes','checklist_qtd_acidentes',
        'checklist_acidente_semelhante','checklist_usava_epi','checklist_epi_justificativa',
        'checklist_treinamento','checklist_experiencia','checklist_supervisor_presente',
        'testemunha1_nome','testemunha2_nome','supervisor_turno_nome',
        'tecnico_seguranca','analise_acidente','descricao_acidente',
        'cel_testemunha1','cel_testemunha2','cel_supervisor','cel_tecnico',
        'responsavel_nome','responsavel_cpf','empresa_terceiro'
    ]
    vals = [dados.get(c) for c in campos]
    rid = dados.get('id')
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
        else:
            cur = conn.cursor()
        if rid:
            if USE_POSTGRES:
                sets = ', '.join(f'{c}=%s' for c in campos)
                cur.execute(f'UPDATE acidentes_relatorios SET {sets} WHERE id=%s', vals + [rid])
            else:
                sets = ', '.join(f'{c}=?' for c in campos)
                cur.execute(f'UPDATE acidentes_relatorios SET {sets} WHERE id=?', vals + [rid])
            conn.commit()
            return int(rid)
        else:
            cols = ', '.join(campos) + ', criado_por'
            if USE_POSTGRES:
                placeholders = ', '.join(['%s'] * (len(campos) + 1))
                cur.execute(f'INSERT INTO acidentes_relatorios ({cols}) VALUES ({placeholders}) RETURNING id',
                            vals + [usuario])
                new_id = cur.fetchone()['id']
            else:
                placeholders = ', '.join(['?'] * (len(campos) + 1))
                cur.execute(f'INSERT INTO acidentes_relatorios ({cols}) VALUES ({placeholders})',
                            vals + [usuario])
                new_id = cur.lastrowid
            conn.commit()
            return new_id
    finally:
        conn.close()


def salvar_plano_acao_acidente(relatorio_id: int, plano: list):
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute('DELETE FROM acidentes_plano_acao WHERE relatorio_id=%s', (relatorio_id,))
            for a in plano:
                cur.execute('INSERT INTO acidentes_plano_acao (relatorio_id,acao,responsavel,prazo,visto) VALUES (%s,%s,%s,%s,%s)',
                            (relatorio_id, a.get('acao',''), a.get('responsavel',''), a.get('prazo',''), a.get('visto','')))
        else:
            cur = conn.cursor()
            cur.execute('DELETE FROM acidentes_plano_acao WHERE relatorio_id=?', (relatorio_id,))
            for a in plano:
                cur.execute('INSERT INTO acidentes_plano_acao (relatorio_id,acao,responsavel,prazo,visto) VALUES (?,?,?,?,?)',
                            (relatorio_id, a.get('acao',''), a.get('responsavel',''), a.get('prazo',''), a.get('visto','')))
        conn.commit()
    finally:
        conn.close()


def listar_relatorios_acidente() -> list:
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
        else:
            cur = conn.cursor()
        cur.execute('SELECT id,funcionario_nome,matricula,tipo_acidente,data_acidente,afastamento,criado_em FROM acidentes_relatorios ORDER BY criado_em DESC')
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def buscar_relatorio_acidente(rid: int) -> dict | None:
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(cursor_factory=_psycopg2_extras.RealDictCursor)
            cur.execute('SELECT * FROM acidentes_relatorios WHERE id=%s', (rid,))
        else:
            cur = conn.cursor()
            cur.execute('SELECT * FROM acidentes_relatorios WHERE id=?', (rid,))
        row = cur.fetchone()
        if not row:
            return None
        r = dict(row)
        if USE_POSTGRES:
            cur.execute('SELECT * FROM acidentes_plano_acao WHERE relatorio_id=%s ORDER BY id', (rid,))
        else:
            cur.execute('SELECT * FROM acidentes_plano_acao WHERE relatorio_id=? ORDER BY id', (rid,))
        r['plano_acao'] = [dict(a) for a in cur.fetchall()]
        return r
    finally:
        conn.close()


def deletar_relatorio_acidente(rid: int):
    conn = conectar()
    try:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute('DELETE FROM acidentes_plano_acao WHERE relatorio_id=%s', (rid,))
            cur.execute('DELETE FROM acidentes_relatorios WHERE id=%s', (rid,))
        else:
            cur = conn.cursor()
            cur.execute('DELETE FROM acidentes_plano_acao WHERE relatorio_id=?', (rid,))
            cur.execute('DELETE FROM acidentes_relatorios WHERE id=?', (rid,))
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    criar_banco()
