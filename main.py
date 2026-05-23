import asyncio
import json
import os
import random
import re
import uuid
from contextlib import asynccontextmanager
from datetime import date
from typing import Dict, List, Optional, Tuple
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, File, UploadFile, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, validator
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from dotenv import load_dotenv
from supabase import create_client, Client
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

load_dotenv()

# ================================================================
# CONFIGURAÇÕES DE AMBIENTE
# ================================================================
BASE_URL = os.environ.get("BASE_URL", "http://localhost:6500")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY")

# Validação obrigatória das variáveis do Supabase (NOVO-04)
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "SUPABASE_URL e SUPABASE_ANON_KEY são obrigatórias. "
        "Configure-as no painel do Render ou no arquivo .env."
    )

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ================================================================
# CONFIGURAÇÃO DO BUCKET PARA AVATARES (NOVO-07)
# ================================================================
def ensure_avatars_bucket():
    try:
        # Tenta buscar o bucket existente
        supabase.storage.get_bucket("avatars")
        print("Bucket 'avatars' já existe.")
    except Exception:
        # Se ocorrer erro (provavelmente 404 porque não existe), então cria
        try:
            print("Criando bucket 'avatars'...")
            supabase.storage.create_bucket("avatars", {"public": True})
        except Exception as e:
            print(f"Erro ao criar bucket: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # A lógica de inicialização vai aqui
    ensure_avatars_bucket()
    yield

app = FastAPI(title="Damas Real - Server-Side Engine com IA", lifespan=lifespan)

# ================================================================
# RATE LIMITING
# ================================================================
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ================================================================
# CORS
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
    email_google  = id_info.get('email', '')
    nome_google   = id_info.get('name', 'Jogador Real')
    picture_google = id_info.get('picture', '')

    profile_data = {
        "google_id": google_id,
        "nome": nome_google,
        "email": email_google,
        "foto_url": picture_google   # inicialmente a foto do Google
    }

    # upsert no Supabase
    supabase.table("perfis").upsert(profile_data, on_conflict="google_id").execute()

    res = supabase.table("perfis").select("nick, saldo, foto_url").eq("google_id", google_id).execute()
    row = res.data[0] if res.data else None

    nick  = row.get("nick") if row else None
    saldo = row.get("saldo", 0.0) if row else 0.0
    foto_final = row.get("foto_url") if row and row.get("foto_url") else picture_google

    # NOVO-02: corrigido retorno
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

    # Upload da foto para o Supabase Storage (NOVO-07)
    foto_url_atual = None
    if foto and foto.filename:
        EXTENSOES_PERMITIDAS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
        content_type_map = {
            'image/jpeg': '.jpg', 'image/png': '.png',
            'image/gif': '.gif', 'image/webp': '.webp'
        }
        ext = os.path.splitext(foto.filename)[1].lower()
        if ext not in EXTENSOES_PERMITIDAS:
            raise HTTPException(status_code=400, detail="Formato de imagem não permitido.")
        ext_final = content_type_map.get(foto.content_type, ext)
        bucket_path = f"avatars/{googleId}{ext_final}"
        foto_bytes = await foto.read()
        supabase.storage.from_("avatars").upload(
            bucket_path, foto_bytes,
            file_options={"content-type": foto.content_type, "upsert": "true"}
        )
        foto_url_atual = supabase.storage.from_("avatars").get_public_url(bucket_path)

    update_data = {
        "bio": perfil_json.get('bio'),
        "telefone": perfil_json.get('telefone'),
        "cpf": cpf_valor,
        "data_nasc": data_nasc,
        "nick": perfil_json.get('nick')
    }
    if foto_url_atual:
        update_data["foto_url"] = foto_url_atual

    supabase.table("perfis").update(update_data).eq("google_id", googleId).execute()

    return {"status": "Perfil salvo com sucesso!", "foto_url": foto_url_atual}

@app.get("/get-profile/{google_id}")
async def get_profile(google_id: str):
    res_profile = supabase.table("perfis").select("*").eq("google_id", google_id).execute()
    if not res_profile.data:
        return {}
    
    data = res_profile.data[0]
    # NOVO-09: usar created_at em vez de data
    res_hist = supabase.table("historico").select("*").eq("google_id", google_id).order("created_at", desc=True).execute()
    data['historico'] = res_hist.data
    return data

@app.post("/registrar-jogo")
async def registrar_jogo(payload: RegistrarJogoPayload):
    partida_id = payload.partida_id or str(uuid.uuid4())

    # Verifica se partida já existe
    res_check = supabase.table("historico").select("id").eq("partida_id", partida_id).execute()
    if res_check.data:
        return {"status": "already_registered", "partida_id": partida_id}

    # Busca saldo
    res_user = supabase.table("perfis").select("saldo").eq("google_id", payload.googleId).execute()
    if not res_user.data:
        raise HTTPException(status_code=404, detail="Jogador não encontrado.")

    saldo_atual = res_user.data[0]["saldo"]
    delta = 0.0
    if payload.resultado == "vitoria":
        delta = +payload.valor
    elif payload.resultado == "derrota":
        delta = -payload.valor
        if saldo_atual + delta < 0:
            raise HTTPException(status_code=422, detail=f"Saldo insuficiente. R$ {saldo_atual:.2f}")

    # Insere histórico
    supabase.table("historico").insert({
        "partida_id": partida_id,
        "google_id": payload.googleId,
        "tipo": payload.tipo,
        "resultado": payload.resultado,
        "valor": payload.valor,
        "delta_saldo": delta
    }).execute()

    # Atualiza saldo
    if delta != 0.0:
        supabase.table("perfis").update({"saldo": saldo_atual + delta}).eq("google_id", payload.googleId).execute()

    return {
        "status": "registered",
        "partida_id": partida_id,
        "delta": delta,
        "novo_saldo": round(saldo_atual + delta, 2)
    }

# ================================================================
# ENGINE DE DAMAS (com correção da recursão - NOVO-05)
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
    def verificar_fim_de_jogo(tabuleiro: List[List[str]], regras: str) -> Optional[str]:
        """
        Retorna 'w' se as brancas venceram, 'b' se as pretas venceram, ou None se o jogo continua.
        O jogo termina se um dos lados não tiver nenhuma peça OU não tiver nenhum movimento válido (captura ou andar).
        """
        brancas_vivas = False
        pretas_vivas = False
        
        # 1. Varredura rápida para ver se alguém ficou totalmente sem peças
        for linha in tabuleiro:
            for p in linha:
                if p.lower() == 'w': brancas_vivas = True
                if p.lower() == 'b': pretas_vivas = True
                
        if not brancas_vivas: return "b"  # Pretas ganham
        if not pretas_vivas: return "w"   # Brancas ganham

        # 2. Verificar se as Brancas têm movimentos ou capturas disponíveis
        brancas_tem_movimento = False
        for r in range(8):
            for c in range(8):
                if tabuleiro[r][c].lower() == 'w':
                    # Se tem captura obrigatória ou qualquer movimento válido, ela ainda joga
                    if DamasEngine.tem_capturas_obrigatorias_da_peca(tabuleiro, r, c, 'w', regras):
                        brancas_tem_movimento = True
                        break
                    # Verifica também movimentos simples (passando continue_capture=False para testar passos)
                    for dr, dc in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
                        rt, ct = r + dr, c + dc
                        if 0 <= rt < 8 and 0 <= ct < 8:
                            # Teste rápido se o motor aceita o movimento simples
                            sucesso, *_ = DamasEngine.validar_e_mover(tabuleiro, r, c, rt, ct, 'w', regras)
                            if sucesso:
                                brancas_tem_movimento = True
                                break
            if brancas_tem_movimento: break

        if not brancas_tem_movimento: return "b"

        # 3. Verificar se as Pretas (IA) têm movimentos ou capturas disponíveis
        pretas_tem_movimento = False
        for r in range(8):
            for c in range(8):
                if tabuleiro[r][c].lower() == 'b':
                    if DamasEngine.tem_capturas_obrigatorias_da_peca(tabuleiro, r, c, 'b', regras):
                        pretas_tem_movimento = True
                        break
                    for dr, dc in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
                        rt, ct = r + dr, c + dc
                        if 0 <= rt < 8 and 0 <= ct < 8:
                            sucesso, *_ = DamasEngine.validar_e_mover(tabuleiro, r, c, rt, ct, 'b', regras)
                            if sucesso:
                                pretas_tem_movimento = True
                                break
            if pretas_tem_movimento: break

        if not pretas_tem_movimento: return "w"

        return None  # O jogo segue normalmente

    @staticmethod
    def tem_capturas_obrigatorias_da_peca(board: List[List[str]], r: int, c: int, cor: str, regras: str) -> bool:
        """Verifica se uma peça específica tem alguma captura válida (respeitando a regra de não ir para trás)."""
        peca = board[r][c]
        if peca == '.' or peca.lower() != cor:
            return False
        is_dama = peca.isupper()
        direcoes = [(-1, -1), (-1, 1), (1, -1), (1, 1)]
        for dr, dc in direcoes:
            if not is_dama:
                # Peça normal não move nem captura para trás conforme solicitado
                if cor == 'w' and dr >= 0: continue
                if cor == 'b' and dr <= 0: continue
                rm, cm = r + dr, c + dc
                rt, ct = r + 2*dr, c + 2*dc
                if 0 <= rt < 8 and 0 <= ct < 8:
                    if board[rm][cm] != '.' and board[rm][cm].lower() != cor and board[rt][ct] == '.':
                        return True
            else:
                # Lógica para Damas
                if regras == "americana":
                    rm, cm = r + dr, c + dc
                    rt, ct = r + 2*dr, c + 2*dc
                    if 0 <= rt < 8 and 0 <= ct < 8:
                        if board[rm][cm] != '.' and board[rm][cm].lower() != cor and board[rt][ct] == '.':
                            return True
                else: # Brasileira (Voo longo)
                    found_enemy = False
                    nr, nc = r + dr, c + dc
                    while 0 <= nr < 8 and 0 <= nc < 8:
                        if board[nr][nc] == '.':
                            if found_enemy: return True
                        elif board[nr][nc].lower() == cor:
                            break
                        else: # inimiga
                            if found_enemy: break # bloqueado por 2ª peça
                            found_enemy = True
                        nr += dr
                        nc += dc
        return False

    @staticmethod
    def jogador_tem_capturas_possiveis(board: List[List[str]], cor: str, regras: str) -> bool:
        """Varre todo o tabuleiro para ver se o jogador tem qualquer captura obrigatória disponível."""
        for r in range(8):
            for c in range(8):
                if DamasEngine.tem_capturas_obrigatorias_da_peca(board, r, c, cor, regras):
                    return True
        return False

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
                for dr, dc in [(-1,-1), (-1,1), (1,-1), (1,1)]:
                    if is_dama and regras == "brasileira":
                        nr, nc = r + dr, c + dc
                        while 0 <= nr < 8 and 0 <= nc < 8:
                            candidatos.add((nr, nc))
                            if board[nr][nc] != '.':
                                nr2, nc2 = nr + dr, nc + dc
                                if 0 <= nr2 < 8 and 0 <= nc2 < 8 and board[nr2][nc2] == '.':
                                    candidatos.add((nr2, nc2))
                                break
                            nr += dr
                            nc += dc
                    else:
                        for dist in [1, 2]:
                            nr, nc = r + dr*dist, c + dc*dist
                            if 0 <= nr < 8 and 0 <= nc < 8:
                                candidatos.add((nr, nc))
                for nr, nc in candidatos:
                    # NOVO-05: passa continue_capture=True para evitar recursão infinita
                    ok, _, _ = DamasEngine.validar_e_mover(board, r, c, nr, nc, player, regras, continue_capture=True)
                    if ok:
                        mov = ((r, c), (nr, nc))
                        if abs(nr - r) >= 2:
                            capturas.append(mov)
                        else:
                            movimentos.append(mov)
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
                _, nb, _ = DamasEngine.validar_e_mover(board, rf, cf, rt, ct, "b", regras, continue_capture=True)
                ev, _    = DamasEngine.minimax(nb, profundidade-1, alpha, beta, False, regras)
                if ev > best: best, melhor = ev, mov
                alpha = max(alpha, ev)
                if beta <= alpha: break
            return best, melhor
        else:
            best = float('inf')
            for mov in movimentos:
                (rf, cf), (rt, ct) = mov
                _, nb, _ = DamasEngine.validar_e_mover(board, rf, cf, rt, ct, "w", regras, continue_capture=True)
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
            # 1. Bloqueio de direção (Não pode andar E não pode comer para trás)
            if peca == 'w' and dr >= 0:
                return False, board, "Peças brancas comuns só movem para a frente (linhas menores)"
            if peca == 'b' and dr <= 0:
                return False, board, "Peças pretas comuns só movem para a frente (linhas maiores)"

            if abs(dr) == 1 and abs(dc) == 1:
                if continue_capture: # Se veio de uma captura combo, não pode apenas andar
                    return False, board, "Precisa continuar capturando"
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

                nb = [row[:] for row in board]
                nb[r_from][c_from] = "."
                nb[rm][cm] = "."
                nb[r_to][c_to] = peca
                if (player == "w" and r_to == 0) or (player == "b" and r_to == 7):
                    nb[r_to][c_to] = player.upper()

                # Verifica se essa peça que acabou de mover ainda tem capturas válidas
                if DamasEngine.tem_capturas_obrigatorias_da_peca(nb, r_to, c_to, player, regras):
                    return True, nb, "MULTI_CAPTURE"
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
                        if DamasEngine.tem_capturas_obrigatorias_da_peca(nb, r_to, c_to, player, regras):
                             return True, nb, "MULTI_CAPTURE"
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
                    if not continue_capture:
                        if DamasEngine.tem_capturas_obrigatorias_da_peca(nb, r_to, c_to, player, regras):
                            return True, nb, "MULTI_CAPTURE"
                    return True, nb, "OK"
                else:
                    return False, board, "Múltiplas peças no caminho."
        return False, board, "Movimento não suportado."

# ================================================================
# GERENCIADOR DE SALAS E WEBSOCKETS (inalterado, exceto chamadas com continue_capture)
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

                # ---- VALIDAÇÃO DE CAPTURA OBRIGATÓRIA ----
                tem_que_comer = DamasEngine.jogador_tem_capturas_possiveis(partida["board"], player_color, partida["regras"])
                movimento_eh_captura = abs(rf - rt) >= 2
                if tem_que_comer and not movimento_eh_captura:
                    await websocket.send_text(json.dumps({
                        "type": "invalid_move",
                        "message": "Movimento inválido! Você é obrigado a capturar uma peça adversária."
                    }))
                    continue
                # ------------------------------------------

                ok, nb, motivo = DamasEngine.validar_e_mover(
                    partida["board"], rf, cf, rt, ct, player_color, partida["regras"])
                if ok:
                    partida["board"] = nb
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
        elif msg.get("type") == "move":
            irf, icf = algebraic_to_index(msg["from"])
            irt, ict = algebraic_to_index(msg["to"])
            
            # 1. VALIDAÇÃO DO JOGADOR (BRANCAS)
            tem_que_comer_w = DamasEngine.jogador_tem_capturas_possiveis(p["board"], "w", p["regras"])
            movimento_eh_captura_w = abs(irf - irt) >= 2
            
            if tem_que_comer_w and not movimento_eh_captura_w:
                await websocket.send_text(json.dumps({
                    "type": "invalid_move", 
                    "message": "Movimento inválido! É obrigado a capturar uma peça adversária."
                }))
                continue
                
            sucesso, resultado_ou_motivo, mais_capturas = DamasEngine.validar_e_mover(
                p["board"], irf, icf, irt, ict, "w", p["regras"]
            )
            
            if sucesso:
                p["board"] = resultado_ou_motivo
                venc = DamasEngine.verificar_fim_de_jogo(p["board"], p["regras"])
                
                if venc:
                    await websocket.send_text(json.dumps({
                        "type": "game_over", "winner": venc, "partida_id": p["partida_id"], "board": p["board"]
                    }))
                    continue
                
                # Se o jogador capturou e ainda pode continuar no combo, mantém o turno dele
                if mais_capturas and movimento_eh_captura_w:
                    await websocket.send_text(json.dumps({
                        "type": "update", "board": p["board"], "turn": "w", "regras": p["regras"]
                    }))
                else:
                    # PASSA O TURNO PARA A IA (PRETAS)
                    p["turn"] = "b"
                    await websocket.send_text(json.dumps({
                        "type": "update", "board": p["board"], "turn": "b", "regras": p["regras"]
                    }))
                    
                    # Delay para dar sensação de pensamento da IA
                    await asyncio.sleep(0.5)
                    
                    # 2. DECISÃO DA IA (PRETAS)
                    # Primeiro, verifica se a IA tem alguma captura obrigatória
                    jogadas_ia = DamasEngine.obter_capturas_possiveis_do_jogador(p["board"], "b", p["regras"])
                    
                    # Se não tiver nenhuma captura obrigatória, ela pode andar para a frente
                    if not jogadas_ia:
                        jogadas_ia = DamasEngine.obter_movimentos_simples_do_jogador(p["board"], "b", p["regras"])
                    
                    if jogadas_ia:
                        # Escolhe uma jogada válida (pode usar random para testar rápido ou integrar com o minimax)
                        # Para garantir que ela mexe agora mesmo, vamos pegar uma jogada válida direta:
                        mov_escolhido = random.choice(jogadas_ia)
                        
                        (irf_b, icf_b), (irt_b, ict_b) = mov_escolhido
                        _, novo_tabuleiro_b, _ = DamasEngine.validar_e_mover(p["board"], irf_b, icf_b, irt_b, ict_b, "b", p["regras"])
                        p["board"] = novo_tabuleiro_b
                    
                    # Devolve o turno para as Brancas
                    p["turn"] = "w"
                    venc = DamasEngine.verificar_fim_de_jogo(p["board"], p["regras"])
                    
                    if venc:
                        await websocket.send_text(json.dumps({
                            "type": "game_over", "winner": venc, "partida_id": p["partida_id"], "board": p["board"]
                        }))
                    else:
                        await websocket.send_text(json.dumps({
                            "type": "update", "board": p["board"], "turn": "w", "regras": p["regras"]
                        }))
            else:
                await websocket.send_text(json.dumps({"type": "invalid_move", "message": resultado_ou_motivo}))
    except WebSocketDisconnect:
        pass

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 6500))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)