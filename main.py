import asyncio
import json
import random
from typing import Dict, List, Tuple
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google.oauth2 import id_token
from google.auth.transport import requests

app = FastAPI(title="Damas Real - Server-Side Engine com IA")

# Isso libera o seu front-end para conversar com o back-end
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GOOGLE_CLIENT_ID = "392676701496-c3tuhektac3snndmob3jb12vbbsifagl.apps.googleusercontent.com"

class AuthToken(BaseModel):
    token: str

@app.post("/auth/google")
async def auth_google(payload: AuthToken):
    try:
        # Descriptografa e valida a integridade do token emitido pelo Google
        id_info = id_token.verify_oauth2_token(
            payload.token, 
            requests.Request(), 
            GOOGLE_CLIENT_ID
        )
        
        return {
            "status": "authenticated",
            "google_id": id_info['sub'],
            "email": id_info['email'],
            "name": id_info.get('name', 'Jogador Real'),
            "picture": id_info.get('picture', '')
        }
    except ValueError:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado.")

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
                    if r < 3:
                        board[r][c] = "b"
                    elif r > 4:
                        board[r][c] = "w"
        return board

    @staticmethod
    def obter_todos_movimentos_validos(board: List[List[str]], player: str, regras: str) -> List[Tuple[Tuple[int, int], Tuple[int, int]]]:
        """Varre o tabuleiro e retorna uma lista de tuplas [((r_de, c_de), (r_para, c_para)), ...]"""
        movimentos = []
        for r in range(8):
            for c in range(8):
                peca = board[r][c]
                if peca.lower() == player:
                    # Varre possíveis destinos no tabuleiro (simplificado para performance)
                    for dr in [-2, -1, 1, 2, -3, -4, -5, -6, -7, 3, 4, 5, 6, 7]:
                        for dc in [-2, -1, 1, 2, -3, -4, -5, -6, -7, 3, 4, 5, 6, 7]:
                            to_r, to_c = r + dr, c + dc
                            if 0 <= to_r < 8 and 0 <= to_c < 8:
                                sucesso, _, _ = DamasEngine.validar_e_mover(board, r, c, to_r, to_c, player, regras)
                                if sucesso:
                                    movimentos.append(((r, c), (to_r, to_c)))
        return movimentos

    @staticmethod
    def avaliar_tabuleiro(board: List[List[str]]) -> int:
        """Função de utilidade/heurística para a IA pontuar a situação do tabuleiro"""
        score = 0
        for r in range(8):
            for c in range(8):
                peca = board[r][c]
                if peca == 'b': score += 10 + (7 - r)  # Incentiva pretas a avançarem
                elif peca == 'B': score += 35
                elif peca == 'w': score -= (10 + r)    # Incentiva brancas a avançarem
                elif peca == 'W': score -= 35
        return score

    @staticmethod
    def minimax(board: List[List[str]], profundidade: int, alpha: float, beta: float, maximizando: bool, regras: str) -> Tuple[int, Tuple[Tuple[int, int], Tuple[int, int]]]:
        """Algoritmo Minimax com Poda Alpha-Beta"""
        if profundidade == 0:
            return DamasEngine.avaliar_tabuleiro(board), None

        player = "b" if maximizando else "w"
        movimentos = DamasEngine.obter_todos_movimentos_validos(board, player, regras)
        
        if not movimentos:
            return DamasEngine.avaliar_tabuleiro(board), None

        melhor_movimento = random.choice(movimentos)

        if maximizando:
            max_eval = -float('inf')
            for mov in movimentos:
                (r_f, c_f), (r_t, c_t) = mov
                _, novo_board, _ = DamasEngine.validar_e_mover(board, r_f, c_f, r_t, c_t, "b", regras)
                ev, _ = DamasEngine.minimax(novo_board, profundidade - 1, alpha, beta, False, regras)
                if ev > max_eval:
                    max_eval = ev
                    melhor_movimento = mov
                alpha = max(alpha, ev)
                if beta <= alpha:
                    break
            return max_eval, melhor_movimento
        else:
            min_eval = float('inf')
            for mov in movimentos:
                (r_f, c_f), (r_t, c_t) = mov
                _, novo_board, _ = DamasEngine.validar_e_mover(board, r_f, c_f, r_t, c_t, "w", regras)
                ev, _ = DamasEngine.minimax(novo_board, profundidade - 1, alpha, beta, True, regras)
                if ev < min_eval:
                    min_eval = ev
                    melhor_movimento = mov
                beta = min(beta, ev)
                if beta <= alpha:
                    break
            return min_eval, melhor_movimento

    @staticmethod
    def validar_e_mover(board: List[List[str]], r_from: int, c_from: int, r_to: int, c_to: int, player: str, regras: str) -> Tuple[bool, List[List[str]], str]:
        peca = board[r_from][c_from]
        
        if peca == "." or peca.lower() != player:
            return False, board, "Você não tem uma peça nessa casa."
        if board[r_to][c_to] != ".":
            return False, board, "A casa de destino não está vazia."
        if (r_to + c_to) % 2 == 0:
            return False, board, "Movimento inválido: peças só jogam nas casas escuras."

        dr = r_to - r_from
        dc = c_to - c_from
        
        # Peças Normais
        if peca in ['w', 'b']:
            if abs(dr) == 1 and abs(dc) == 1:
                if regras == "americana":
                    if player == "w" and dr > 0: return False, board, "Peças comuns não recuam."
                    if player == "b" and dr < 0: return False, board, "Peças comuns não recuam."
                
                novo_board = [row[:] for row in board]
                novo_board[r_from][c_from] = "."
                novo_board[r_to][c_to] = peca
                
                if (player == "w" and r_to == 0) or (player == "b" and r_to == 7):
                    novo_board[r_to][c_to] = player.upper()
                return True, novo_board, "OK"

            elif abs(dr) == 2 and abs(dc) == 2:
                r_mid = r_from + dr // 2
                c_mid = c_from + dc // 2
                peca_capturada = board[r_mid][c_mid]
                
                if peca_capturada == "." or peca_capturada.lower() == player:
                    return False, board, "Não há peça adversária para capturar."
                
                if regras == "americana":
                    if player == "w" and dr > 0: return False, board, "Capturas devem ser para frente."
                    if player == "b" and dr < 0: return False, board, "Capturas devem ser para frente."

                novo_board = [row[:] for row in board]
                novo_board[r_from][c_from] = "."
                novo_board[r_mid][c_mid] = "."
                novo_board[r_to][c_to] = peca
                
                if (player == "w" and r_to == 0) or (player == "b" and r_to == 7):
                    novo_board[r_to][c_to] = player.upper()
                return True, novo_board, "OK"
                
        # Damas
        elif peca in ['W', 'B']:
            if abs(dr) != abs(dc):
                return False, board, "Damas só se movem na diagonal."
            
            step_r = 1 if dr > 0 else -1
            step_c = 1 if dc > 0 else -1
            
            if regras == "americana":
                if abs(dr) == 1:
                    novo_board = [row[:] for row in board]
                    novo_board[r_from][c_from] = "."
                    novo_board[r_to][c_to] = peca
                    return True, novo_board, "OK"
                elif abs(dr) == 2:
                    r_mid = r_from + step_r
                    c_mid = c_from + step_c
                    if board[r_mid][c_mid] != "." and board[r_mid][c_mid].lower() != player:
                        novo_board = [row[:] for row in board]
                        novo_board[r_from][c_from] = "."
                        novo_board[r_mid][c_mid] = "."
                        novo_board[r_to][c_to] = peca
                        return True, novo_board, "OK"
                return False, board, "Dama americana só move curto alcance."
                
            elif regras == "brasileira":
                pecas_no_caminho = []
                curr_r, curr_c = r_from + step_r, c_from + step_c
                while curr_r != r_to:
                    if board[curr_r][curr_c] != ".":
                        pecas_no_caminho.append((curr_r, curr_c, board[curr_r][curr_c]))
                    curr_r += step_r
                    curr_c += step_c
                
                if len(pecas_no_caminho) == 0:
                    novo_board = [row[:] for row in board]
                    novo_board[r_from][c_from] = "."
                    novo_board[r_to][c_to] = peca
                    return True, novo_board, "OK"
                elif len(pecas_no_caminho) == 1:
                    rcap, ccap, pcap = pecas_no_caminho[0]
                    if pcap.lower() == player:
                        return False, board, "Você não pode pular sua própria peça."
                    novo_board = [row[:] for row in board]
                    novo_board[r_from][c_from] = "."
                    novo_board[rcap][ccap] = "."
                    novo_board[r_to][c_to] = peca
                    return True, novo_board, "OK"
                else:
                    return False, board, "Múltiplas peças no caminho."

        return False, board, "Movimento não suportado."

class GerenciadorSalas:
    def __init__(self):
        self.partidas: Dict[str, dict] = {}
        self.conexoes: Dict[str, List[WebSocket]] = {}

    async def conectar(self, game_id: str, websocket: WebSocket):
        await websocket.accept()
        if game_id not in self.conexoes:
            self.conexoes[game_id] = []
        self.conexoes[game_id].append(websocket)

    def desconectar(self, game_id: str, websocket: WebSocket):
        if game_id in self.conexoes:
            self.conexoes[game_id].remove(websocket)

    async def broadcast(self, game_id: str, mensagem: dict):
        if game_id in self.conexoes:
            for ws in self.conexoes[game_id]:
                try: await ws.send_text(json.dumps(mensagem))
                except Exception: pass

salas = GerenciadorSalas()

# ENDPOINT HUMANO VS HUMANO (Existente)
@app.websocket("/ws/partida/{game_id}/{player_color}")
async def websocket_endpoint(websocket: WebSocket, game_id: str, player_color: str):
    await salas.conectar(game_id, websocket)
    if game_id not in salas.partidas:
        salas.partidas[game_id] = {"board": DamasEngine.criar_tabuleiro_inicial(), "turn": "w", "regras": "brasileira"}
    
    await websocket.send_text(json.dumps({"type": "init", "board": salas.partidas[game_id]["board"], "turn": salas.partidas[game_id]["turn"], "regras": salas.partidas[game_id]["regras"]}))

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            partida = salas.partidas[game_id]
            
            if msg.get("type") == "move" and partida["turn"] == player_color:
                r_from, c_from = algebraic_to_index(msg.get("from"))
                r_to, c_to = algebraic_to_index(msg.get("to"))
                sucesso, novo_board, motivo = DamasEngine.validar_e_mover(partida["board"], r_from, c_from, r_to, c_to, player_color, partida["regras"])
                if sucesso:
                    partida["board"] = novo_board
                    partida["turn"] = "b" if player_color == "w" else "w"
                    await salas.broadcast(game_id, {"type": "update", "board": partida["board"], "turn": partida["turn"], "regras": partida["regras"]})
                else:
                    await websocket.send_text(json.dumps({"type": "invalid_move", "message": motivo}))
    except WebSocketDisconnect:
        salas.desconectar(game_id, websocket)

# =====================================================================
# NOVO ENDPOINT: JOGADOR VS IA (A IA joga sempre com as Pretas 'b')
# =====================================================================
# =====================================================================
# NOVO ENDPOINT: JOGADOR VS IA (A IA joga sempre com as Pretas 'b')
# =====================================================================
@app.websocket("/ws/ia/{game_id}")
async def websocket_ia_endpoint(websocket: WebSocket, game_id: str):
    await websocket.accept()
    
    # Nova partida local contra a IA
    partida_ia = {
        "board": DamasEngine.criar_tabuleiro_inicial(),
        "turn": "w",  # Jogador Humano começa de Brancas
        "regras": "brasileira"
    }
    
    await websocket.send_text(json.dumps({
        "type": "init", "board": partida_ia["board"], "turn": partida_ia["turn"], "regras": partida_ia["regras"]
    }))

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            
            if msg.get("type") == "config_rules":
                partida_ia["regras"] = msg.get("regras", "brasileira")
                # Corrigido: Envia o feedback visual imediatamente para o front-end sincronizar
                await websocket.send_text(json.dumps({
                    "type": "update", "board": partida_ia["board"], "turn": partida_ia["turn"], "regras": partida_ia["regras"]
                }))
                continue
            
            if msg.get("type") == "move" and partida_ia["turn"] == "w":
                r_from, c_from = algebraic_to_index(msg.get("from"))
                r_to, c_to = algebraic_to_index(msg.get("to"))
                
                # Executa turno Humano
                sucesso, novo_board, motivo = DamasEngine.validar_e_mover(
                    partida_ia["board"], r_from, c_from, r_to, c_to, "w", partida_ia["regras"]
                )
                
                if sucesso:
                    partida_ia["board"] = novo_board
                    partida_ia["turn"] = "b" # Passa para a IA
                    
                    # Atualiza o player com a jogada que ele acabou de fazer
                    await websocket.send_text(json.dumps({
                        "type": "update", "board": partida_ia["board"], "turn": "b", "regras": partida_ia["regras"]
                    }))
                    
                    # --- VEZ DA IA PENSAR ---
                    await asyncio.sleep(0.4) # Delayzinho humano artificial
                    
                    # Profundidade 4 Minimax
                    _, melhor_mov = DamasEngine.minimax(partida_ia["board"], profundidade=4, alpha=-float('inf'), beta=float('inf'), maximizando=True, regras=partida_ia["regras"])
                    
                    if melhor_mov:
                        (ia_rf, ia_cf), (ia_rt, ia_ct) = melhor_mov
                        _, board_pos_ia, _ = DamasEngine.validar_e_mover(partida_ia["board"], ia_rf, ia_cf, ia_rt, ia_ct, "b", partida_ia["regras"])
                        partida_ia["board"] = board_pos_ia
                    
                    partida_ia["turn"] = "w" # Devolve o turno pro humano
                    
                    # Despacha o estado final pós-IA de volta pro front
                    await websocket.send_text(json.dumps({
                        "type": "update", "board": partida_ia["board"], "turn": "w", "regras": partida_ia["regras"]
                    }))
                else:
                    await websocket.send_text(json.dumps({"type": "invalid_move", "message": motivo}))
                    
    except WebSocketDisconnect:
        pass

if __name__ == "__main__":
    import uvicorn
    # Porta 6500 está totalmente liberada pelos navegadores
    uvicorn.run("main:app", host="0.0.0.0", port=6500, reload=True)