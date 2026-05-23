import asyncio
import json
import os
import random
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date
from typing import Dict, List, Optional, Tuple

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles          # ← NOVO: serve fotos de perfil
from pydantic import BaseModel, Field, validator
from google.oauth2 import id_token
from google.auth.transport import requests

app = FastAPI(title="Damas Real - Server-Side Engine com IA")

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Serve a pasta de uploads como rota estática
# → Foto acessível em: http://localhost:6500/uploads/<nome_do_arquivo>
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


# ================================================================
# BUG CRÍTICO CORRIGIDO #1 — isolation_level=None + conn.commit()
#
# PROBLEMA ORIGINAL:
#   conn = sqlite3.connect('...', isolation_level=None)
#   → isolation_level=None = modo autocommit
#   → conn.commit() e conn.rollback() são NO-OPS silenciosos
#   → os dados nunca eram gravados no banco
#
# SOLUÇÃO:
#   Remover isolation_level=None.
#   Com isolation_level padrão (""), o Python gerencia BEGIN/COMMIT.
#   Para transações exclusivas (apostas), usamos conn.execute("BEGIN IMMEDIATE")
#   e depois conn.commit() / conn.rollback() normalmente.
# ================================================================
@contextmanager
def get_db():
    """
    Context manager para conexões SQLite com isolamento correto.
    isolation_level padrão ativa o gerenciamento de transações do Python:
    conn.commit() e conn.rollback() funcionam corretamente.
    """
    conn = sqlite3.connect('damas_real.db')   # isolation_level padrão
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")    # leituras concorrentes
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def setup_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS perfis (
                google_id   TEXT PRIMARY KEY,
                nome        TEXT,
                email       TEXT,
                nick        TEXT,
                bio         TEXT,
                telefone    TEXT,
                cpf         TEXT,
                data_nasc   TEXT,
                saldo       REAL    NOT NULL DEFAULT 0.0,
                foto_path   TEXT,
                foto_url    TEXT
            );

            CREATE TABLE IF NOT EXISTS historico (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                partida_id   TEXT    UNIQUE,
                google_id    TEXT    NOT NULL,
                tipo         TEXT    NOT NULL,
                resultado    TEXT    NOT NULL,
                valor        REAL    NOT NULL DEFAULT 0.0,
                delta_saldo  REAL    NOT NULL DEFAULT 0.0,
                data         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (google_id) REFERENCES perfis(google_id)
            );
        """)
        # Migração segura: adiciona colunas novas se o banco já existia
        _migrar_colunas(conn)
        conn.commit()


def _migrar_colunas(conn):
    """Adiciona colunas sem quebrar bancos existentes."""
    colunas_existentes = {
        row[1] for row in conn.execute("PRAGMA table_info(perfis)")
    }
    novas = {
        "nome":     "ALTER TABLE perfis ADD COLUMN nome TEXT",
        "email":    "ALTER TABLE perfis ADD COLUMN email TEXT",
        "nick":     "ALTER TABLE perfis ADD COLUMN nick TEXT",
        "foto_url": "ALTER TABLE perfis ADD COLUMN foto_url TEXT",
    }
    for col, sql in novas.items():
        if col not in colunas_existentes:
            conn.execute(sql)


setup_db()


# ================================================================
# BUG CORRIGIDO #2 — CORS bloqueando localhost vs 127.0.0.1
#
# PROBLEMA ORIGINAL:
#   allow_origins=["http://127.0.0.1:5500"]
#   → Se o navegador abre localhost:5500 (diferente de 127.0.0.1:5500),
#     o CORS bloqueia todas as requisições silenciosamente.
# ================================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://127.0.0.1:5501",
        "http://localhost:5501",
        "https://damareal1.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GOOGLE_CLIENT_ID = "392676701496-c3tuhektac3snndmob3jb12vbbsifagl.apps.googleusercontent.com"


# ================================================================
# MODELOS PYDANTIC
# ================================================================
class AuthToken(BaseModel):
    token: str


class RegistrarJogoPayload(BaseModel):
    googleId:   str
    tipo:       str
    resultado:  str
    valor:      float = Field(default=0.0, ge=0)
    partida_id: Optional[str] = None

    @validator('tipo')
    def tipo_valido(cls, v):
        if v not in ('ia', 'online', 'aposta'):
            raise ValueError("tipo deve ser 'ia', 'online' ou 'aposta'")
        return v

    @validator('resultado')
    def resultado_valido(cls, v):
        if v not in ('vitoria', 'derrota', 'empate'):
            raise ValueError("resultado deve ser 'vitoria', 'derrota' ou 'empate'")
        return v


# ================================================================
# AUTH
# BUG CORRIGIDO #3 — login não devolvia nick/saldo armazenados
#
# PROBLEMA ORIGINAL:
#   O endpoint retornava apenas dados do token Google (nome/foto/email).
#   Nick e saldo salvos no banco eram ignorados — a UI mostrava
#   sempre o nome do Google, nunca o apelido personalizado.
#
# SOLUÇÃO:
#   Após o upsert, busca o perfil completo do banco e mescla os dados.
# ================================================================
@app.post("/auth/google")
async def auth_google(payload: AuthToken):
    try:
        id_info = id_token.verify_oauth2_token(
            payload.token,
            requests.Request(),
            GOOGLE_CLIENT_ID
        )
    except ValueError:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado.")

    google_id     = id_info['sub']
    nome_google   = id_info.get('name', 'Jogador Real')
    email_google  = id_info.get('email', '')
    picture_google = id_info.get('picture', '')

    with get_db() as conn:
        # Cria perfil se não existir (INSERT OR IGNORE preserva saldo)
        conn.execute(
            """INSERT OR IGNORE INTO perfis (google_id, nome, email, foto_url)
               VALUES (?, ?, ?, ?)""",
            (google_id, nome_google, email_google, picture_google)
        )
        # Atualiza nome/email/foto do Google sempre que logar
        # (o usuário pode ter mudado o avatar no Google)
        conn.execute(
            """UPDATE perfis
                  SET nome = ?, email = ?, foto_url = ?
                WHERE google_id = ?""",
            (nome_google, email_google, picture_google, google_id)
        )
        conn.commit()

        # Busca perfil completo para retornar nick e saldo ao front
        row = conn.execute(
            "SELECT nick, saldo, foto_path FROM perfis WHERE google_id = ?",
            (google_id,)
        ).fetchone()

    nick  = (row["nick"] if row and row["nick"] else None)
    saldo = (row["saldo"] if row else 0.0)

    # foto_path local tem prioridade sobre URL do Google
    foto_final = picture_google
    if row and row["foto_path"]:
        nome_arquivo = os.path.basename(row["foto_path"])
        foto_final = f"http://localhost:6500/uploads/{nome_arquivo}"

    return {
        "status":    "authenticated",
        "google_id": google_id,
        "email":     email_google,
        "name":      nome_google,
        "nick":      nick,               # ← apelido salvo no banco
        "picture":   foto_final,         # ← foto local ou URL Google
        "saldo":     round(saldo, 2),    # ← saldo atual
    }


# ================================================================
# PERFIL
# ================================================================
@app.post("/update-profile")
async def update_profile(
    foto:     UploadFile = File(None),
    dados:    str = Form(...),
    googleId: str = Form(...)
):
    perfil_json = json.loads(dados)

    data_nasc = perfil_json.get("dataNascimento")
    if data_nasc:
        born  = date.fromisoformat(data_nasc)
        today = date.today()
        age   = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        if age < 18:
            raise HTTPException(status_code=400, detail="Usuário menor de idade.")

    foto_path = None
    if foto and foto.filename:
        # Sanitiza nome do arquivo
        ext = os.path.splitext(foto.filename)[1].lower()
        safe_name = f"{googleId}{ext}"
        foto_path = os.path.join(UPLOAD_DIR, safe_name)
        with open(foto_path, "wb") as buffer:
            shutil.copyfileobj(foto.file, buffer)

    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO perfis (google_id) VALUES (?)", (googleId,)
        )
        if foto_path:
            conn.execute(
                """UPDATE perfis
                      SET bio = ?, telefone = ?, cpf = ?, data_nasc = ?,
                          nick = ?, foto_path = ?
                    WHERE google_id = ?""",
                (perfil_json.get('bio'), perfil_json.get('telefone'),
                 perfil_json.get('cpf'), data_nasc,
                 perfil_json.get('nick'), foto_path, googleId)
            )
        else:
            conn.execute(
                """UPDATE perfis
                      SET bio = ?, telefone = ?, cpf = ?, data_nasc = ?, nick = ?
                    WHERE google_id = ?""",
                (perfil_json.get('bio'), perfil_json.get('telefone'),
                 perfil_json.get('cpf'), data_nasc,
                 perfil_json.get('nick'), googleId)
            )
        conn.commit()

    # Retorna a URL pública da foto para o front atualizar imediatamente
    foto_url = None
    if foto_path:
        nome_arquivo = os.path.basename(foto_path)
        foto_url = f"http://localhost:6500/uploads/{nome_arquivo}"

    return {"status": "Perfil salvo com sucesso!", "foto_url": foto_url}


@app.get("/get-profile/{google_id}")
async def get_profile(google_id: str):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM perfis WHERE google_id = ?", (google_id,)
        ).fetchone()

        if not row:
            return {}

        data = dict(row)

        # Gera URL pública da foto se houver arquivo local
        if data.get("foto_path"):
            nome_arquivo = os.path.basename(data["foto_path"])
            data["foto_url"] = f"http://localhost:6500/uploads/{nome_arquivo}"

        historico = conn.execute(
            """SELECT tipo, resultado, valor, delta_saldo, data
                 FROM historico
                WHERE google_id = ?
             ORDER BY data DESC""",
            (google_id,)
        ).fetchall()
        data['historico'] = [dict(r) for r in historico]

    return data


# ================================================================
# REGISTRAR JOGO — transação atômica com BEGIN IMMEDIATE
# ================================================================
@app.post("/registrar-jogo")
async def registrar_jogo(payload: RegistrarJogoPayload):
    partida_id = payload.partida_id or str(uuid.uuid4())

    with get_db() as conn:
        # BEGIN IMMEDIATE: bloqueia escritas concorrentes agora,
        # não só na primeira escrita. Essencial para apostas.
        conn.execute("BEGIN IMMEDIATE")

        ja_existe = conn.execute(
            "SELECT id FROM historico WHERE partida_id = ?", (partida_id,)
        ).fetchone()

        if ja_existe:
            conn.rollback()
            return {"status": "already_registered", "partida_id": partida_id}

        row = conn.execute(
            "SELECT saldo FROM perfis WHERE google_id = ?", (payload.googleId,)
        ).fetchone()

        if not row:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Jogador não encontrado.")

        saldo_atual = row["saldo"]

        if payload.resultado == "vitoria":
            delta = +payload.valor
        elif payload.resultado == "derrota":
            delta = -payload.valor
            if saldo_atual + delta < 0:
                conn.rollback()
                raise HTTPException(
                    status_code=422,
                    detail=f"Saldo insuficiente. Saldo atual: R$ {saldo_atual:.2f}"
                )
        else:
            delta = 0.0

        conn.execute(
            """INSERT INTO historico
                   (partida_id, google_id, tipo, resultado, valor, delta_saldo)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (partida_id, payload.googleId, payload.tipo,
             payload.resultado, payload.valor, delta)
        )

        if delta != 0.0:
            conn.execute(
                "UPDATE perfis SET saldo = saldo + ? WHERE google_id = ?",
                (delta, payload.googleId)
            )

        conn.commit()

        return {
            "status":     "registered",
            "partida_id": partida_id,
            "delta":      delta,
            "novo_saldo": round(saldo_atual + delta, 2)
        }


# ================================================================
# ENGINE DE DAMAS
# ================================================================
def algebraic_to_index(coord: str) -> Tuple[int, int]:
    col = ord(coord[0].upper()) - ord('A')
    row = 8 - int(coord[1])
    return row, col

def index_to_algebraic(row: int, col: int) -> str:
    return f"{chr(ord('A') + col)}{8 - row}"


class DamasEngine:
    @staticmethod
    def criar_tabuleiro_inicial() -> List[List[str]]:
        board = [["." for _ in range(8)] for _ in range(8)]
        for r in range(8):
            for c in range(8):
                if (r + c) % 2 == 1:
                    if r < 3:   board[r][c] = "b"
                    elif r > 4: board[r][c] = "w"
        return board

    @staticmethod
    def verificar_fim_de_jogo(board: List[List[str]], regras: str) -> Optional[str]:
        movs_w  = DamasEngine.obter_todos_movimentos_validos(board, "w", regras)
        movs_b  = DamasEngine.obter_todos_movimentos_validos(board, "b", regras)
        pecas_w = sum(1 for row in board for cell in row if cell.lower() == 'w')
        pecas_b = sum(1 for row in board for cell in row if cell.lower() == 'b')
        if pecas_w == 0 or not movs_w: return "b"
        if pecas_b == 0 or not movs_b: return "w"
        return None

    @staticmethod
    def obter_todos_movimentos_validos(board, player, regras):
        movimentos = []
        for r in range(8):
            for c in range(8):
                if board[r][c].lower() == player:
                    for dr in [-7,-6,-5,-4,-3,-2,-1,1,2,3,4,5,6,7]:
                        for dc in [-7,-6,-5,-4,-3,-2,-1,1,2,3,4,5,6,7]:
                            to_r, to_c = r + dr, c + dc
                            if 0 <= to_r < 8 and 0 <= to_c < 8:
                                ok, _, _ = DamasEngine.validar_e_mover(board, r, c, to_r, to_c, player, regras)
                                if ok:
                                    movimentos.append(((r, c), (to_r, to_c)))
        return movimentos

    @staticmethod
    def avaliar_tabuleiro(board):
        score = 0
        for r in range(8):
            for c in range(8):
                p = board[r][c]
                if p == 'b':   score += 10 + (7 - r)
                elif p == 'B': score += 35
                elif p == 'w': score -= (10 + r)
                elif p == 'W': score -= 35
        return score

    @staticmethod
    def minimax(board, profundidade, alpha, beta, maximizando, regras):
        if profundidade == 0:
            return DamasEngine.avaliar_tabuleiro(board), None
        player = "b" if maximizando else "w"
        movimentos = DamasEngine.obter_todos_movimentos_validos(board, player, regras)
        if not movimentos:
            return DamasEngine.avaliar_tabuleiro(board), None
        melhor = random.choice(movimentos)
        if maximizando:
            best = -float('inf')
            for mov in movimentos:
                (rf, cf), (rt, ct) = mov
                _, nb, _ = DamasEngine.validar_e_mover(board, rf, cf, rt, ct, "b", regras)
                ev, _    = DamasEngine.minimax(nb, profundidade-1, alpha, beta, False, regras)
                if ev > best: best, melhor = ev, mov
                alpha = max(alpha, ev)
                if beta <= alpha: break
            return best, melhor
        else:
            best = float('inf')
            for mov in movimentos:
                (rf, cf), (rt, ct) = mov
                _, nb, _ = DamasEngine.validar_e_mover(board, rf, cf, rt, ct, "w", regras)
                ev, _    = DamasEngine.minimax(nb, profundidade-1, alpha, beta, True, regras)
                if ev < best: best, melhor = ev, mov
                beta = min(beta, ev)
                if beta <= alpha: break
            return best, melhor

    @staticmethod
    def validar_e_mover(board, r_from, c_from, r_to, c_to, player, regras):
        peca = board[r_from][c_from]
        if peca == "." or peca.lower() != player:
            return False, board, "Você não tem uma peça nessa casa."
        if board[r_to][c_to] != ".":
            return False, board, "A casa de destino não está vazia."
        if (r_to + c_to) % 2 == 0:
            return False, board, "Movimento inválido: peças só jogam nas casas escuras."

        dr = r_to - r_from
        dc = c_to - c_from

        if peca in ['w', 'b']:
            if abs(dr) == 1 and abs(dc) == 1:
                if regras == "americana":
                    if player == "w" and dr > 0: return False, board, "Peças comuns não recuam."
                    if player == "b" and dr < 0: return False, board, "Peças comuns não recuam."
                nb = [row[:] for row in board]
                nb[r_from][c_from] = "."
                nb[r_to][c_to] = peca
                if (player == "w" and r_to == 0) or (player == "b" and r_to == 7):
                    nb[r_to][c_to] = player.upper()
                return True, nb, "OK"
            elif abs(dr) == 2 and abs(dc) == 2:
                rm, cm = r_from + dr//2, c_from + dc//2
                pc = board[rm][cm]
                if pc == "." or pc.lower() == player:
                    return False, board, "Não há peça adversária para capturar."
                if regras == "americana":
                    if player == "w" and dr > 0: return False, board, "Capturas devem ser para frente."
                    if player == "b" and dr < 0: return False, board, "Capturas devem ser para frente."
                nb = [row[:] for row in board]
                nb[r_from][c_from] = "."
                nb[rm][cm] = "."
                nb[r_to][c_to] = peca
                if (player == "w" and r_to == 0) or (player == "b" and r_to == 7):
                    nb[r_to][c_to] = player.upper()
                return True, nb, "OK"

        elif peca in ['W', 'B']:
            if abs(dr) != abs(dc):
                return False, board, "Damas só se movem na diagonal."
            sr = 1 if dr > 0 else -1
            sc = 1 if dc > 0 else -1

            if regras == "americana":
                if abs(dr) == 1:
                    nb = [row[:] for row in board]
                    nb[r_from][c_from] = "."
                    nb[r_to][c_to] = peca
                    return True, nb, "OK"
                elif abs(dr) == 2:
                    rm, cm = r_from + sr, c_from + sc
                    if board[rm][cm] != "." and board[rm][cm].lower() != player:
                        nb = [row[:] for row in board]
                        nb[r_from][c_from] = "."
                        nb[rm][cm] = "."
                        nb[r_to][c_to] = peca
                        return True, nb, "OK"
                return False, board, "Dama americana só move curto alcance."

            elif regras == "brasileira":
                caminho = []
                cr, cc = r_from + sr, c_from + sc
                while cr != r_to:
                    if board[cr][cc] != ".":
                        caminho.append((cr, cc, board[cr][cc]))
                    cr += sr
                    cc += sc
                if len(caminho) == 0:
                    nb = [row[:] for row in board]
                    nb[r_from][c_from] = "."
                    nb[r_to][c_to] = peca
                    return True, nb, "OK"
                elif len(caminho) == 1:
                    rcap, ccap, pcap = caminho[0]
                    if pcap.lower() == player:
                        return False, board, "Você não pode pular sua própria peça."
                    nb = [row[:] for row in board]
                    nb[r_from][c_from] = "."
                    nb[rcap][ccap] = "."
                    nb[r_to][c_to] = peca
                    return True, nb, "OK"
                else:
                    return False, board, "Múltiplas peças no caminho."

        return False, board, "Movimento não suportado."


# ================================================================
# GERENCIADOR DE SALAS
# ================================================================
class GerenciadorSalas:
    def __init__(self):
        self.partidas: Dict[str, dict] = {}
        self.conexoes: Dict[str, List[WebSocket]] = {}

    async def conectar(self, game_id, ws):
        await ws.accept()
        self.conexoes.setdefault(game_id, []).append(ws)

    def desconectar(self, game_id, ws):
        if game_id in self.conexoes:
            self.conexoes[game_id].remove(ws)

    async def broadcast(self, game_id, msg):
        for ws in self.conexoes.get(game_id, []):
            try: await ws.send_text(json.dumps(msg))
            except Exception: pass


salas = GerenciadorSalas()


@app.websocket("/ws/partida/{game_id}/{player_color}")
async def websocket_endpoint(websocket: WebSocket, game_id: str, player_color: str):
    await salas.conectar(game_id, websocket)
    if game_id not in salas.partidas:
        salas.partidas[game_id] = {
            "board":      DamasEngine.criar_tabuleiro_inicial(),
            "turn":       "w",
            "regras":     "brasileira",
            "partida_id": str(uuid.uuid4())
        }
    partida = salas.partidas[game_id]
    await websocket.send_text(json.dumps({
        "type": "init", "board": partida["board"],
        "turn": partida["turn"], "regras": partida["regras"]
    }))
    try:
        while True:
            msg = json.loads(await websocket.receive_text())
            if msg.get("type") == "move" and partida["turn"] == player_color:
                rf, cf = algebraic_to_index(msg["from"])
                rt, ct = algebraic_to_index(msg["to"])
                ok, nb, motivo = DamasEngine.validar_e_mover(
                    partida["board"], rf, cf, rt, ct, player_color, partida["regras"])
                if ok:
                    partida["board"] = nb
                    partida["turn"]  = "b" if player_color == "w" else "w"
                    venc = DamasEngine.verificar_fim_de_jogo(nb, partida["regras"])
                    if venc:
                        await salas.broadcast(game_id, {
                            "type": "game_over", "winner": venc,
                            "partida_id": partida["partida_id"], "board": nb
                        })
                    else:
                        await salas.broadcast(game_id, {
                            "type": "update", "board": nb,
                            "turn": partida["turn"], "regras": partida["regras"]
                        })
                else:
                    await websocket.send_text(json.dumps({"type": "invalid_move", "message": motivo}))
            elif msg.get("type") == "config_rules":
                partida["regras"] = msg.get("regras", "brasileira")
                await salas.broadcast(game_id, {
                    "type": "update", "board": partida["board"],
                    "turn": partida["turn"], "regras": partida["regras"]
                })
    except WebSocketDisconnect:
        salas.desconectar(game_id, websocket)


@app.websocket("/ws/ia/{game_id}")
async def websocket_ia_endpoint(websocket: WebSocket, game_id: str):
    await websocket.accept()
    p = {
        "board":      DamasEngine.criar_tabuleiro_inicial(),
        "turn":       "w",
        "regras":     "brasileira",
        "partida_id": str(uuid.uuid4())
    }
    await websocket.send_text(json.dumps({
        "type": "init", "board": p["board"], "turn": p["turn"], "regras": p["regras"]
    }))
    try:
        while True:
            msg = json.loads(await websocket.receive_text())
            if msg.get("type") == "config_rules":
                p["regras"] = msg.get("regras", "brasileira")
                await websocket.send_text(json.dumps({
                    "type": "update", "board": p["board"], "turn": p["turn"], "regras": p["regras"]
                }))
                continue
            if msg.get("type") == "move" and p["turn"] == "w":
                rf, cf = algebraic_to_index(msg["from"])
                rt, ct = algebraic_to_index(msg["to"])
                ok, nb, motivo = DamasEngine.validar_e_mover(p["board"], rf, cf, rt, ct, "w", p["regras"])
                if ok:
                    p["board"] = nb
                    p["turn"]  = "b"
                    venc = DamasEngine.verificar_fim_de_jogo(nb, p["regras"])
                    if venc:
                        await websocket.send_text(json.dumps({
                            "type": "game_over", "winner": venc,
                            "partida_id": p["partida_id"], "board": nb
                        }))
                        continue
                    await websocket.send_text(json.dumps({
                        "type": "update", "board": p["board"], "turn": "b", "regras": p["regras"]
                    }))
                    await asyncio.sleep(0.4)
                    _, mov = DamasEngine.minimax(p["board"], 4, -float('inf'), float('inf'), True, p["regras"])
                    if mov:
                        (irf, icf), (irt, ict) = mov
                        _, p["board"], _ = DamasEngine.validar_e_mover(p["board"], irf, icf, irt, ict, "b", p["regras"])
                    p["turn"] = "w"
                    venc = DamasEngine.verificar_fim_de_jogo(p["board"], p["regras"])
                    if venc:
                        await websocket.send_text(json.dumps({
                            "type": "game_over", "winner": venc,
                            "partida_id": p["partida_id"], "board": p["board"]
                        }))
                    else:
                        await websocket.send_text(json.dumps({
                            "type": "update", "board": p["board"], "turn": "w", "regras": p["regras"]
                        }))
                else:
                    await websocket.send_text(json.dumps({"type": "invalid_move", "message": motivo}))
    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    import uvicorn
    # O Render atribui uma porta dinâmica via variável de ambiente PORT.
    # Caso não encontre (localmente), utiliza a 6500.
    port = int(os.environ.get("PORT", 6500))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)