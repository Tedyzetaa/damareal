import asyncio
import json
import os
import random
import re
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date
from typing import Dict, List, Optional, Tuple
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, File, UploadFile, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, validator
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from dotenv import load_dotenv
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

load_dotenv()

app = FastAPI(title="Damas Real - Server-Side Engine com IA")

# ================================================================
# CONFIGURAÇÕES DE AMBIENTE
# ================================================================
BASE_URL = os.environ.get("BASE_URL", "http://localhost:6500")
DB_PATH = os.environ.get("DB_PATH", "damas_real.db")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# ================================================================
# RATE LIMITING
# ================================================================
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ================================================================
# CORS (inclui domínio do Render)
# ================================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://127.0.0.1:5501",
        "http://localhost:5501",
        "https://damareal1-2ml7.onrender.com",
        "https://damareal1.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================================================================
# BANCO DE DADOS (com suporte a DB_PATH via env)
# ================================================================
@contextmanager
def get_db():
    """Conexão padrão com gerenciamento de transação automático."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

@contextmanager
def get_db_exclusive():
    """Conexão com autocommit para controle manual (apostas)."""
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
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
        _migrar_colunas(conn)
        conn.commit()

def _migrar_colunas(conn):
    colunas_existentes = {row[1] for row in conn.execute("PRAGMA table_info(perfis)")}
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
# VALIDAÇÕES
# ================================================================
def validar_cpf(cpf: str) -> bool:
    cpf = re.sub(r'[^\d]', '', cpf)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for i in range(9, 11):
        soma = sum(int(cpf[j]) * (i + 1 - j) for j in range(i))
        resto = (soma * 10) % 11
        if resto in (10, 11):
            resto = 0
        if resto != int(cpf[i]):
            return False
    return True

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
        # Aceita 'apostada' também (frontend) e normaliza internamente
        if v not in ('ia', 'online', 'aposta', 'apostada'):
            raise ValueError("tipo deve ser 'ia', 'online', 'aposta' ou 'apostada'")
        return 'aposta' if v == 'apostada' else v

    @validator('resultado')
    def resultado_valido(cls, v):
        if v not in ('vitoria', 'derrota', 'empate'):
            raise ValueError("resultado deve ser 'vitoria', 'derrota' ou 'empate'")
        return v

# ================================================================
# ENDPOINTS
# ================================================================
@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/auth/google")
@limiter.limit("10/minute")
async def auth_google(request: Request, payload: AuthToken):
    try:
        id_info = id_token.verify_oauth2_token(
            payload.token,
            google_requests.Request(),
            GOOGLE_CLIENT_ID
        )
    except ValueError:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado.")

    google_id     = id_info['sub']
    nome_google   = id_info.get('name', 'Jogador Real')
    email_google  = id_info.get('email', '')
    picture_google = id_info.get('picture', '')

    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO perfis (google_id, nome, email, foto_url) VALUES (?, ?, ?, ?)",
            (google_id, nome_google, email_google, picture_google)
        )
        conn.execute(
            "UPDATE perfis SET nome = ?, email = ?, foto_url = ? WHERE google_id = ?",
            (nome_google, email_google, picture_google, google_id)
        )
        conn.commit()
        row = conn.execute(
            "SELECT nick, saldo, foto_path FROM perfis WHERE google_id = ?",
            (google_id,)
        ).fetchone()

    nick  = row["nick"] if row and row["nick"] else None
    saldo = row["saldo"] if row else 0.0

    foto_final = picture_google
    if row and row["foto_path"]:
        nome_arquivo = os.path.basename(row["foto_path"])
        foto_final = f"{BASE_URL}/uploads/{nome_arquivo}"

    return {
        "status":    "authenticated",
        "google_id": google_id,
        "email":     email_google,
        "name":      nome_google,
        "nick":      nick,
        "picture":   foto_final,
        "saldo":     round(saldo, 2),
    }

@app.post("/update-profile")
async def update_profile(
    foto:     UploadFile = File(None),
    dados:    str = Form(...),
    googleId: str = Form(...)
):
    perfil_json = json.loads(dados)

    # Validação de idade
    data_nasc = perfil_json.get("dataNascimento")
    if data_nasc:
        born  = date.fromisoformat(data_nasc)
        today = date.today()
        age   = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        if age < 18:
            raise HTTPException(status_code=400, detail="Usuário menor de idade.")

    # Validação de CPF
    cpf_valor = perfil_json.get("cpf", "")
    if cpf_valor and not validar_cpf(cpf_valor):
        raise HTTPException(status_code=400, detail="CPF inválido.")

    # Validação de extensão de imagem
    EXTENSOES_PERMITIDAS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
    content_type_map = {
        'image/jpeg': '.jpg', 'image/png': '.png',
        'image/gif': '.gif', 'image/webp': '.webp'
    }
    foto_path = None
    if foto and foto.filename:
        ext = os.path.splitext(foto.filename)[1].lower()
        if ext not in EXTENSOES_PERMITIDAS:
            raise HTTPException(status_code=400, detail="Formato de imagem não permitido.")
        # Força extensão baseada no content-type
        ext_final = content_type_map.get(foto.content_type, ext)
        safe_name = f"{googleId}{ext_final}"
        foto_path = os.path.join(UPLOAD_DIR, safe_name)
        with open(foto_path, "wb") as buffer:
            shutil.copyfileobj(foto.file, buffer)

    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO perfis (google_id) VALUES (?)", (googleId,))
        if foto_path:
            conn.execute(
                """UPDATE perfis
                      SET bio = ?, telefone = ?, cpf = ?, data_nasc = ?,
                          nick = ?, foto_path = ?
                    WHERE google_id = ?""",
                (perfil_json.get('bio'), perfil_json.get('telefone'),
                 cpf_valor, data_nasc,
                 perfil_json.get('nick'), foto_path, googleId)
            )
        else:
            conn.execute(
                """UPDATE perfis
                      SET bio = ?, telefone = ?, cpf = ?, data_nasc = ?, nick = ?
                    WHERE google_id = ?""",
                (perfil_json.get('bio'), perfil_json.get('telefone'),
                 cpf_valor, data_nasc,
                 perfil_json.get('nick'), googleId)
            )
        conn.commit()

    foto_url = None
    if foto_path:
        nome_arquivo = os.path.basename(foto_path)
        foto_url = f"{BASE_URL}/uploads/{nome_arquivo}"

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
        if data.get("foto_path"):
            nome_arquivo = os.path.basename(data["foto_path"])
            data["foto_url"] = f"{BASE_URL}/uploads/{nome_arquivo}"
        historico = conn.execute(
            """SELECT tipo, resultado, valor, delta_saldo, data
                 FROM historico
                WHERE google_id = ?
             ORDER BY data DESC""",
            (google_id,)
        ).fetchall()
        data['historico'] = [dict(r) for r in historico]
    return data

@app.post("/registrar-jogo")
async def registrar_jogo(payload: RegistrarJogoPayload):
    partida_id = payload.partida_id or str(uuid.uuid4())

    with get_db_exclusive() as conn:
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
        delta = 0.0
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
# ENGINE DE DAMAS (com otimização e multi-captura)
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
        movs_w = DamasEngine.obter_todos_movimentos_validos(board, "w", regras)
        movs_b = DamasEngine.obter_todos_movimentos_validos(board, "b", regras)
        pecas_w = sum(1 for row in board for cell in row if cell.lower() == 'w')
        pecas_b = sum(1 for row in board for cell in row if cell.lower() == 'b')
        if pecas_w == 0 or not movs_w: return "b"
        if pecas_b == 0 or not movs_b: return "w"
        return None

    @staticmethod
    def obter_todos_movimentos_validos(board, player, regras):
        movimentos = []
        capturas = []
        for r in range(8):
            for c in range(8):
                peca = board[r][c]
                if peca == '.' or peca.lower() != player:
                    continue
                is_dama = peca.isupper()
                candidatos = set()
                # Direções diagonais
                for dr, dc in [(-1,-1), (-1,1), (1,-1), (1,1)]:
                    if is_dama and regras == "brasileira":
                        # Dama: varre toda a diagonal
                        nr, nc = r + dr, c + dc
                        while 0 <= nr < 8 and 0 <= nc < 8:
                            candidatos.add((nr, nc))
                            if board[nr][nc] != '.':
                                # Pode capturar a peça adversária
                                nr2, nc2 = nr + dr, nc + dc
                                if 0 <= nr2 < 8 and 0 <= nc2 < 8 and board[nr2][nc2] == '.':
                                    candidatos.add((nr2, nc2))
                                break
                            nr += dr
                            nc += dc
                    else:
                        # Peça comum ou dama americana: máximo 2 casas
                        for dist in [1, 2]:
                            nr, nc = r + dr*dist, c + dc*dist
                            if 0 <= nr < 8 and 0 <= nc < 8:
                                candidatos.add((nr, nc))
                # Validar cada candidato
                for nr, nc in candidatos:
                    ok, _, _ = DamasEngine.validar_e_mover(board, r, c, nr, nc, player, regras)
                    if ok:
                        mov = ((r, c), (nr, nc))
                        if abs(nr - r) >= 2:
                            capturas.append(mov)
                        else:
                            movimentos.append(mov)
        # Captura obrigatória
        return capturas if capturas else movimentos

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
    def validar_e_mover(board, r_from, c_from, r_to, c_to, player, regras, continue_capture=False):
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
                capturadas = []
                while cr != r_to:
                    if board[cr][cc] != ".":
                        caminho.append((cr, cc, board[cr][cc]))
                    cr += sr
                    cc += sc
                # Verifica captura única (apenas uma peça no caminho)
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
                    # Verifica se há captura múltipla para continuar
                    if not continue_capture:
                        # Simula continuação para ver se a mesma peça pode capturar novamente
                        temp_nb = [row[:] for row in nb]
                        novas_capturas = DamasEngine.obter_todos_movimentos_validos(temp_nb, player, regras)
                        for mov in novas_capturas:
                            (rf, cf), (rt, ct) = mov
                            if (rf, cf) == (r_to, c_to) and abs(rt - r_to) >= 2:
                                return True, nb, "MULTI_CAPTURE"
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
            try:
                self.conexoes[game_id].remove(ws)
            except ValueError:
                pass

    async def broadcast(self, game_id, msg):
        for ws in self.conexoes.get(game_id, []):
            try:
                await ws.send_text(json.dumps(msg))
            except Exception:
                pass

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
                    # Se for multi-captura, não troca o turno
                    if motivo == "MULTI_CAPTURE":
                        await salas.broadcast(game_id, {
                            "type": "update", "board": nb,
                            "turn": player_color, "regras": partida["regras"],
                            "must_continue": True, "piece": [rt, ct]
                        })
                    else:
                        partida["turn"] = "b" if player_color == "w" else "w"
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
                    if motivo == "MULTI_CAPTURE":
                        await websocket.send_text(json.dumps({
                            "type": "update", "board": p["board"], "turn": "w", "regras": p["regras"],
                            "must_continue": True, "piece": [rt, ct]
                        }))
                        continue
                    p["turn"] = "b"
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
    port = int(os.environ.get("PORT", 6500))
    # Em produção, use reload=False conforme instruções (Procfile)
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)