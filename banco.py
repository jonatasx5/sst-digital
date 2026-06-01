"""
Banco de dados SQLite do sistema SST Digital.
Tabelas: funcionarios, funcao_documentos, lotes, lote_funcionarios, envios
"""

import sqlite3
import os
from config import DB_PATH


def conectar():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def criar_banco():
    """Cria todas as tabelas se não existirem."""
    conn = conectar()
    c = conn.cursor()

    # ── Funcionários ──────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS funcionarios (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nome        TEXT    NOT NULL,
            cpf         TEXT    NOT NULL UNIQUE,
            matricula   TEXT,
            cargo       TEXT    NOT NULL,
            lotacao     TEXT,
            admissao    TEXT,
            celular     TEXT,
            email       TEXT,
            ativo       INTEGER DEFAULT 1,
            criado_em   TEXT    DEFAULT (datetime('now','localtime'))
        )
    """)

    # ── Matriz Função × Documentos ────────────────────────
    # Guarda quais documentos cada cargo recebe
    c.execute("""
        CREATE TABLE IF NOT EXISTS funcao_documentos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            cargo       TEXT    NOT NULL,
            doc_id      TEXT    NOT NULL,
            UNIQUE(cargo, doc_id)
        )
    """)

    # ── Lotes de envio ────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS lotes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            descricao   TEXT,
            criado_em   TEXT    DEFAULT (datetime('now','localtime')),
            status      TEXT    DEFAULT 'pendente'
        )
    """)

    # ── Funcionários em cada lote ─────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS lote_funcionarios (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            lote_id         INTEGER NOT NULL REFERENCES lotes(id),
            funcionario_id  INTEGER NOT NULL REFERENCES funcionarios(id),
            status          TEXT    DEFAULT 'pendente',
            UNIQUE(lote_id, funcionario_id)
        )
    """)

    # ── Envios individuais (um por doc por funcionário) ───
    c.execute("""
        CREATE TABLE IF NOT EXISTS envios (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            lote_funcionario_id INTEGER REFERENCES lote_funcionarios(id),
            funcionario_id      INTEGER REFERENCES funcionarios(id),
            doc_id              TEXT    NOT NULL,
            doc_nome            TEXT,
            pdf_path            TEXT,
            autentique_id       TEXT,
            link_assinatura     TEXT,
            status              TEXT    DEFAULT 'pendente',
            enviado_em          TEXT,
            assinado_em         TEXT
        )
    """)

    conn.commit()
    conn.close()
    print("✅ Banco criado/verificado com sucesso.")


# ══════════════════════════════════════════════════════════
#  FUNCIONÁRIOS
# ══════════════════════════════════════════════════════════

def importar_funcionarios(lista: list[dict]) -> tuple[int, int]:
    """
    Importa lista de funcionários do Excel.
    Faz UPSERT pelo CPF (atualiza se já existir).
    Retorna (inseridos, atualizados).
    """
    conn = conectar()
    c = conn.cursor()
    inseridos = atualizados = 0

    for f in lista:
        cpf = f.get("cpf", "").strip()
        if not cpf:
            continue
        existe = c.execute("SELECT id FROM funcionarios WHERE cpf=?", (cpf,)).fetchone()
        if existe:
            c.execute("""
                UPDATE funcionarios SET
                    nome=?, cargo=?, lotacao=?, admissao=?,
                    celular=?, email=?, ativo=1
                WHERE cpf=?
            """, (
                f.get("nome",""), f.get("cargo",""), f.get("lotacao",""),
                f.get("admissao",""), f.get("celular",""), f.get("email",""), cpf
            ))
            atualizados += 1
        else:
            c.execute("""
                INSERT INTO funcionarios (nome, cpf, matricula, cargo, lotacao, admissao, celular, email)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                f.get("nome",""), cpf, f.get("matricula",""),
                f.get("cargo",""), f.get("lotacao",""), f.get("admissao",""),
                f.get("celular",""), f.get("email","")
            ))
            inseridos += 1

    conn.commit()
    conn.close()
    return inseridos, atualizados


def buscar_funcionarios(termo: str = "", apenas_ativos: bool = True) -> list:
    conn = conectar()
    c = conn.cursor()
    filtro_ativo = "AND ativo=1" if apenas_ativos else ""
    rows = c.execute(f"""
        SELECT * FROM funcionarios
        WHERE (nome LIKE ? OR cpf LIKE ? OR cargo LIKE ? OR lotacao LIKE ?)
        {filtro_ativo}
        ORDER BY nome
    """, (f"%{termo}%",) * 4).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def buscar_funcionario_por_cpf(cpf: str) -> dict | None:
    conn = conectar()
    r = conn.execute("SELECT * FROM funcionarios WHERE cpf=?", (cpf,)).fetchone()
    conn.close()
    return dict(r) if r else None


def salvar_funcionario(dados: dict) -> int:
    conn = conectar()
    c = conn.cursor()
    if dados.get("id"):
        c.execute("""
            UPDATE funcionarios SET
                nome=?, cpf=?, matricula=?, cargo=?, lotacao=?,
                admissao=?, celular=?, email=?
            WHERE id=?
        """, (
            dados["nome"], dados["cpf"], dados.get("matricula",""),
            dados["cargo"], dados.get("lotacao",""), dados.get("admissao",""),
            dados.get("celular",""), dados.get("email",""), dados["id"]
        ))
        fid = dados["id"]
    else:
        c.execute("""
            INSERT INTO funcionarios (nome, cpf, matricula, cargo, lotacao, admissao, celular, email)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            dados["nome"], dados["cpf"], dados.get("matricula",""),
            dados["cargo"], dados.get("lotacao",""), dados.get("admissao",""),
            dados.get("celular",""), dados.get("email","")
        ))
        fid = c.lastrowid
    conn.commit()
    conn.close()
    return fid


# ══════════════════════════════════════════════════════════
#  MATRIZ FUNÇÃO × DOCUMENTOS
# ══════════════════════════════════════════════════════════

def salvar_docs_cargo(cargo: str, doc_ids: list[str]):
    """Salva quais documentos um cargo recebe."""
    conn = conectar()
    c = conn.cursor()
    c.execute("DELETE FROM funcao_documentos WHERE cargo=?", (cargo,))
    for doc_id in doc_ids:
        c.execute("INSERT OR IGNORE INTO funcao_documentos (cargo, doc_id) VALUES (?,?)", (cargo, doc_id))
    conn.commit()
    conn.close()


def docs_do_cargo(cargo: str) -> list[str]:
    """Retorna lista de doc_ids configurados para um cargo."""
    conn = conectar()
    rows = conn.execute(
        "SELECT doc_id FROM funcao_documentos WHERE cargo=? ORDER BY doc_id",
        (cargo,)
    ).fetchall()
    conn.close()
    return [r["doc_id"] for r in rows]


def listar_cargos_configurados() -> list[str]:
    conn = conectar()
    rows = conn.execute(
        "SELECT DISTINCT cargo FROM funcao_documentos ORDER BY cargo"
    ).fetchall()
    conn.close()
    return [r["cargo"] for r in rows]


# ══════════════════════════════════════════════════════════
#  LOTES
# ══════════════════════════════════════════════════════════

def criar_lote(descricao: str = "") -> int:
    conn = conectar()
    c = conn.cursor()
    c.execute("INSERT INTO lotes (descricao) VALUES (?)", (descricao,))
    lote_id = c.lastrowid
    conn.commit()
    conn.close()
    return lote_id


def adicionar_ao_lote(lote_id: int, funcionario_id: int):
    conn = conectar()
    conn.execute(
        "INSERT OR IGNORE INTO lote_funcionarios (lote_id, funcionario_id) VALUES (?,?)",
        (lote_id, funcionario_id)
    )
    conn.commit()
    conn.close()


def funcionarios_do_lote(lote_id: int) -> list:
    conn = conectar()
    rows = conn.execute("""
        SELECT f.*, lf.id as lf_id, lf.status as lf_status
        FROM lote_funcionarios lf
        JOIN funcionarios f ON f.id = lf.funcionario_id
        WHERE lf.lote_id = ?
        ORDER BY f.nome
    """, (lote_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def listar_lotes() -> list:
    conn = conectar()
    rows = conn.execute("""
        SELECT l.*,
               COUNT(lf.id) as total_func,
               SUM(CASE WHEN lf.status='enviado' THEN 1 ELSE 0 END) as enviados
        FROM lotes l
        LEFT JOIN lote_funcionarios lf ON lf.lote_id = l.id
        GROUP BY l.id
        ORDER BY l.criado_em DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════
#  ENVIOS
# ══════════════════════════════════════════════════════════

def registrar_envio(dados: dict) -> int:
    conn = conectar()
    c = conn.cursor()
    c.execute("""
        INSERT INTO envios
            (lote_funcionario_id, funcionario_id, doc_id, doc_nome,
             pdf_path, autentique_id, link_assinatura, status, enviado_em)
        VALUES (?,?,?,?,?,?,?,?,datetime('now','localtime'))
    """, (
        dados.get("lote_funcionario_id"), dados["funcionario_id"],
        dados["doc_id"], dados.get("doc_nome",""),
        dados.get("pdf_path",""), dados.get("autentique_id",""),
        dados.get("link_assinatura",""), dados.get("status","enviado")
    ))
    eid = c.lastrowid
    conn.commit()
    conn.close()
    return eid


def envios_do_funcionario(funcionario_id: int) -> list:
    conn = conectar()
    rows = conn.execute("""
        SELECT e.*, l.descricao as lote_desc, l.criado_em as lote_data
        FROM envios e
        LEFT JOIN lote_funcionarios lf ON lf.id = e.lote_funcionario_id
        LEFT JOIN lotes l ON l.id = lf.lote_id
        WHERE e.funcionario_id = ?
        ORDER BY e.enviado_em DESC
    """, (funcionario_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def atualizar_status_envio(envio_id: int, status: str, link: str = None):
    conn = conectar()
    if link:
        conn.execute(
            "UPDATE envios SET status=?, link_assinatura=? WHERE id=?",
            (status, link, envio_id)
        )
    else:
        conn.execute("UPDATE envios SET status=? WHERE id=?", (status, envio_id))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    criar_banco()
