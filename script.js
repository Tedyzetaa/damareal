// ============================================================
// ESTADO GLOBAL
// ============================================================
let ws              = null;
let selectedSquare  = null;
let currentBoard    = [];
let meuTurno        = false;
let modoAtual       = '';
let lastBoard       = null;
let capturedByWhite = 0;
let capturedByBlack = 0;
let partidaIdAtual    = null;
let partidaRegistrada = false;

let userProfile = {
    googleId:      null,
    nick:          "",
    bio:           "",
    telefone:      "",
    cpf:           "",
    dataNascimento:"",
    privacidade:   { telefone: false, cpf: false }
};

let historicoJogos = [];
const API = 'http://localhost:6500';

// ============================================================
// TOAST
// ============================================================
function showToast(msg, type = 'info', duration = 3500) {
    const icons = { error: '✕', success: '✓', info: '◆' };
    const container = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.innerHTML = `<span class="toast-icon">${icons[type] || '◆'}</span><span class="toast-msg">${msg}</span>`;
    container.appendChild(el);
    setTimeout(() => {
        el.style.animation = 'toastOut 0.3s cubic-bezier(0.22,1,0.36,1) forwards';
        setTimeout(() => el.remove(), 300);
    }, duration);
}

// ============================================================
// NAVEGAÇÃO
// ============================================================
function mudarTela(screenId) {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    document.getElementById(screenId).classList.add('active');
}

// ============================================================
// COORDENADAS DO TABULEIRO
// ============================================================
function renderizarCoordenadas() {
    const letras  = document.getElementById('coordLetters');
    const numeros = document.getElementById('coordNumbers');
    letras.innerHTML  = '';
    numeros.innerHTML = '';
    ['A','B','C','D','E','F','G','H'].forEach(l => {
        const d = document.createElement('div');
        d.className = 'coord-label';
        d.textContent = l;
        letras.appendChild(d);
    });
    for (let i = 8; i >= 1; i--) {
        const d = document.createElement('div');
        d.className = 'coord-label';
        d.textContent = i;
        numeros.appendChild(d);
    }
}

// ============================================================
// RENDERIZAR TABULEIRO
// ============================================================
function renderBoard() {
    const boardDiv = document.getElementById('board');
    boardDiv.innerHTML = '';
    for (let r = 0; r < 8; r++) {
        for (let c = 0; c < 8; c++) {
            const sq    = document.createElement('div');
            const isDark = (r + c) % 2 === 1;
            sq.className = `square ${isDark ? 'dark' : 'light'}`;
            const peca  = (currentBoard[r] && currentBoard[r][c]) ? currentBoard[r][c] : '.';
            if (selectedSquare && selectedSquare.row === r && selectedSquare.col === c)
                sq.classList.add('selected-sq');
            if (peca !== '.') {
                const p = document.createElement('div');
                p.className = `piece ${peca}`;
                if (lastBoard && lastBoard[r] && lastBoard[r][c] !== peca)
                    p.classList.add('just-placed');
                if (selectedSquare && selectedSquare.row === r && selectedSquare.col === c)
                    p.classList.add('selected-piece');
                sq.appendChild(p);
            }
            sq.onclick = () => handleSquareClick(r, c);
            boardDiv.appendChild(sq);
        }
    }
}

// ============================================================
// CLIQUE NO TABULEIRO
// ============================================================
function handleSquareClick(r, c) {
    if (!meuTurno) return;
    const peca    = currentBoard[r] ? currentBoard[r][c] : '.';
    const minhaCor = document.getElementById('playerColor').value;
    if (!selectedSquare) {
        if (peca !== '.' && peca.toLowerCase() === minhaCor) {
            selectedSquare = { row: r, col: c };
            renderBoard();
        }
    } else {
        if (selectedSquare.row === r && selectedSquare.col === c) {
            selectedSquare = null; renderBoard(); return;
        }
        if (peca !== '.' && peca.toLowerCase() === minhaCor) {
            selectedSquare = { row: r, col: c }; renderBoard(); return;
        }
        ws.send(JSON.stringify({
            type: 'move',
            from: indexToAlgebraic(selectedSquare.row, selectedSquare.col),
            to:   indexToAlgebraic(r, c)
        }));
        selectedSquare = null;
    }
}

function indexToAlgebraic(r, c) {
    return String.fromCharCode(65 + c) + (8 - r).toString();
}

// ============================================================
// PEÇAS CAPTURADAS
// ============================================================
function contarPecas(board) {
    let w = 0, b = 0;
    for (let r = 0; r < 8; r++)
        for (let c = 0; c < 8; c++) {
            const p = board[r] ? board[r][c] : '.';
            if (p === 'w' || p === 'W') w++;
            if (p === 'b' || p === 'B') b++;
        }
    return { w, b };
}
function atualizarCapturados2(prevBoard, newBoard) {
    if (!prevBoard) return;
    const prev = contarPecas(prevBoard);
    const curr = contarPecas(newBoard);
    capturedByWhite += Math.max(0, prev.b - curr.b);
    capturedByBlack += Math.max(0, prev.w - curr.w);
    renderCaptureDots();
}
function renderCaptureDots() {
    const byWhite = document.getElementById('capturedByWhite');
    const byBlack = document.getElementById('capturedByBlack');
    byWhite.innerHTML = ''; byBlack.innerHTML = '';
    for (let i = 0; i < 12; i++) {
        const dW = document.createElement('div');
        dW.className = `capture-dot ${i < capturedByWhite ? 'black' : 'empty'}`;
        byWhite.appendChild(dW);
        const dB = document.createElement('div');
        dB.className = `capture-dot ${i < capturedByBlack ? 'white' : 'empty'}`;
        byBlack.appendChild(dB);
    }
}

// ============================================================
// STATUS / INDICADOR
// ============================================================
function atualizarStatus(turno, ehMeuTurno, modo) {
    const ind  = document.getElementById('turnIndicator');
    const stat = document.getElementById('status');
    if (modo === 'ia' && turno === 'b') {
        ind.className    = 'turn-indicator thinking';
        stat.textContent = 'IA calculando...';
    } else if (ehMeuTurno) {
        ind.className    = 'turn-indicator ' + (turno === 'w' ? 'white' : 'black');
        stat.textContent = `Seu turno — ${turno === 'w' ? 'Brancas' : 'Pretas'}`;
    } else {
        ind.className    = 'turn-indicator ' + (turno === 'w' ? 'white' : 'black');
        stat.textContent = 'Aguardando adversário...';
    }
}

// ============================================================
// WEBSOCKET HELPERS
// ============================================================
function onMensagemServidor(data, minhaCor) {
    if (data.type === 'init' || data.type === 'update') {
        const sel = document.getElementById('rulesSelectGame');
        if (sel) sel.value = data.regras;
        atualizarCapturados2(lastBoard, data.board);
        lastBoard    = currentBoard.map(r => [...r]);
        currentBoard = data.board;
        meuTurno     = (data.turn === minhaCor);
        atualizarStatus(data.turn, meuTurno, modoAtual);
        renderBoard();
        if (data.alerta) showToast(data.alerta, 'info');
    } else if (data.type === 'invalid_move' || data.type === 'error') {
        showToast(data.message, 'error');
        selectedSquare = null;
        renderBoard();
    } else if (data.type === 'game_over') {
        partidaIdAtual    = data.partida_id || null;
        partidaRegistrada = false;
        if (data.board) {
            lastBoard    = currentBoard.map(r => [...r]);
            currentBoard = data.board;
            renderBoard();
        }
        abrirModalFimJogo(data.winner, minhaCor);
    }
}

// ============================================================
// JOGAR CONTRA IA
// ============================================================
function jogarContraIA() {
    modoAtual = 'ia'; capturedByWhite = 0; capturedByBlack = 0;
    lastBoard = null; partidaIdAtual = null; partidaRegistrada = false;
    document.getElementById('playerColor').value = 'w';
    if (ws) ws.close();
    const gameId = 'ia_room_' + Math.floor(Math.random() * 99999);
    const wsProtocol = API.startsWith('https') ? 'wss' : 'ws';
    ws = new WebSocket(`${wsProtocol}://${API.split('://')[1]}/ws/ia/${gameId}`);
    ws.onopen    = () => { mudarTela('screenGame'); renderizarCoordenadas(); renderCaptureDots(); showToast('Conectado! Boa sorte.', 'success'); };
    ws.onmessage = (e) => onMensagemServidor(JSON.parse(e.data), 'w');
    ws.onerror   = ()  => showToast('Erro de conexão com o servidor.', 'error');
}

// ============================================================
// LOBBY → ARENA
// ============================================================
function irParaLobby(modo) {
    modoAtual = modo;
    document.getElementById('lobbyTitle').textContent =
        modo === 'online' ? 'Matchmaking Online' : 'Mesa Apostada — Lockstep';
    if (modo === 'apostada')
        showToast('Modo apostado: infraestrutura financeira será ativada ao conectar.', 'info', 5000);
    mudarTela('screenLobby');
}
function conectarServidor() {
    const gameId = document.getElementById('gameId').value.trim();
    const color  = document.getElementById('playerColor').value;
    const rules  = document.getElementById('rulesSelectLobby').value;
    if (!gameId) { showToast('Informe o ID da sala.', 'error'); return; }
    capturedByWhite = 0; capturedByBlack = 0; lastBoard = null;
    partidaIdAtual = null; partidaRegistrada = false;
    if (ws) ws.close();
    const wsProtocol = API.startsWith('https') ? 'wss' : 'ws';
    ws = new WebSocket(`${wsProtocol}://${API.split('://')[1]}/ws/partida/${gameId}/${color}`);
    ws.onopen    = () => { mudarTela('screenGame'); renderizarCoordenadas(); renderCaptureDots(); ws.send(JSON.stringify({ type: 'config_rules', regras: rules })); showToast('Conectado!', 'success'); };
    ws.onmessage = (e) => onMensagemServidor(JSON.parse(e.data), color);
    ws.onerror   = ()  => showToast('Falha ao conectar com o servidor.', 'error');
}
function mudarRegras(novaRegra) {
    if (ws && ws.readyState === WebSocket.OPEN)
        ws.send(JSON.stringify({ type: 'config_rules', regras: novaRegra }));
}

// ============================================================
// ABANDONO
// ============================================================
function confirmarAbandono() {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay open';
    overlay.innerHTML = `
        <div class="modal-box">
            <div class="modal-trophy" style="font-size:40px;">🏳</div>
            <div class="modal-title" style="font-size:1.3rem;">Abandonar?</div>
            <div class="modal-subtitle">Você perderá a partida atual.</div>
            <div class="modal-actions">
                <button class="btn-modal-secondary" onclick="this.closest('.modal-overlay').remove()">Continuar</button>
                <button class="btn-modal-primary"   onclick="abandonarPartida()">Abandonar</button>
            </div>
        </div>`;
    document.body.appendChild(overlay);
}
function abandonarPartida() {
    document.querySelectorAll('.modal-overlay').forEach(m => m.remove());
    if (ws) ws.close();
    mudarTela('screenMenu');
}

// ============================================================
// MODAL FIM DE JOGO
// ============================================================
function abrirModalFimJogo(winner, minhaCor) {
    const trophy   = document.getElementById('modalTrophy');
    const title    = document.getElementById('modalTitle');
    const subtitle = document.getElementById('modalSubtitle');
    let resultado;
    if (winner === minhaCor) {
        trophy.textContent   = '🏆';
        title.textContent    = 'Vitória!';
        subtitle.textContent = modoAtual === 'ia' ? 'Você venceu a IA!' : `As ${winner === 'w' ? 'Brancas' : 'Pretas'} venceram.`;
        resultado = 'vitoria';
    } else if (winner === 'empate') {
        trophy.textContent   = '🤝';
        title.textContent    = 'Empate';
        subtitle.textContent = 'A partida terminou sem vencedor.';
        resultado = 'empate';
    } else {
        trophy.textContent   = modoAtual === 'ia' ? '💀' : '🏆';
        title.textContent    = modoAtual === 'ia' ? 'Derrota' : 'Vitória!';
        subtitle.textContent = modoAtual === 'ia' ? 'A IA venceu desta vez.' : `As ${winner === 'w' ? 'Brancas' : 'Pretas'} venceram.`;
        resultado = 'derrota';
    }
    document.getElementById('modalFimJogo').classList.add('open');
    registrarJogoNoServidor(resultado);
}
function fecharModal() {
    document.getElementById('modalFimJogo').classList.remove('open');
}

// ============================================================
// REGISTRAR JOGO NO BACKEND
// ============================================================
async function registrarJogoNoServidor(resultado, valorAposta = 0) {
    if (!userProfile.googleId) return;
    if (partidaRegistrada) return;
    partidaRegistrada = true;
    const payload = {
        googleId:   userProfile.googleId,
        tipo:       modoAtual || 'online',
        resultado,
        valor:      valorAposta,
        partida_id: partidaIdAtual
    };
    try {
        const res  = await fetch(`${API}/registrar-jogo`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            const data = await res.json();
            if (data.status === 'registered') {
                showToast(`Partida registrada! Saldo: R$ ${data.novo_saldo.toFixed(2)}`, 'success');
                historicoJogos.unshift({ tipo: payload.tipo, resultado, valor: valorAposta, data: new Date().toLocaleDateString() });
                renderHistorico();
            }
        } else {
            const err = await res.json();
            showToast(`Erro ao registrar: ${err.detail}`, 'error');
            partidaRegistrada = false;
        }
    } catch (e) {
        showToast('Não foi possível registrar a partida.', 'error');
        partidaRegistrada = false;
    }
}

// ============================================================
// GOOGLE AUTH
// ============================================================
function inicializarBotaoGoogle() {
    if (typeof google === 'undefined' || !google.accounts?.id) {
        setTimeout(inicializarBotaoGoogle, 100);
        return;
    }
    google.accounts.id.initialize({
        client_id:              CONFIG.GOOGLE_CLIENT_ID,
        callback:               handleCredentialResponse,
        // ── BUG CORRIGIDO #4 — sem auto_select, o usuário tinha que
        //    clicar em "fazer login" toda vez que recarregava a página.
        //    Com auto_select: true, o Google reusa o último login se
        //    o usuário já tinha autenticado antes.
        auto_select:            true,
        cancel_on_tap_outside:  false,
    });
    google.accounts.id.renderButton(
        document.getElementById('googleBtnContainer'),
        { theme: 'filled_black', size: 'medium', type: 'standard', shape: 'pill', width: 200 }
    );

    // ── BUG CORRIGIDO #5 — sem prompt(), o auto_select nunca dispara.
    //    O prompt() tenta fazer login silencioso imediatamente ao carregar.
    //    Se o usuário já tinha sessão, handleCredentialResponse é chamado
    //    automaticamente sem nenhum clique.
    google.accounts.id.prompt((notification) => {
        // Se o prompt foi dispensado ou falhou, mostra o botão normalmente
        if (notification.isNotDisplayed() || notification.isSkippedMoment()) {
            console.info('Auto-login não disponível, exibindo botão.');
        }
    });
}

async function handleCredentialResponse(response) {
    try {
        // Salva o token para reuso (ex: refresh token flows futuros)
        sessionStorage.setItem('dr_credential', response.credential);

        const res = await fetch(`${API}/auth/google`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ token: response.credential })
        });

        if (!res.ok) {
            showToast('Erro ao validar credenciais do Google.', 'error');
            return;
        }

        const userData = await res.json();

        // ── BUG CORRIGIDO #6 — userProfile.googleId era definido pelo
        //    JWT local (payload.sub) antes da resposta do servidor.
        //    Agora usamos o google_id retornado pelo servidor, garantindo
        //    que o ID está alinhado com o registro no banco.
        userProfile.googleId = userData.google_id;

        // Atualiza a barra de autenticação
        document.getElementById('googleBtnContainer').style.display = 'none';
        const ui = document.getElementById('userInfo');
        ui.style.display = 'flex';

        // Mostra nick salvo se existir, senão o nome do Google
        const nomeExibido = userData.nick || userData.name;
        document.getElementById('userName').textContent = nomeExibido;
        document.getElementById('userPicture').src      = userData.picture;

        showToast(`Bem-vindo, ${nomeExibido}!`, 'success');

        // Carrega perfil completo do banco (campos do formulário + histórico)
        await carregarPerfilDoServidor(userData.google_id);

    } catch (err) {
        showToast('Não foi possível comunicar com o servidor.', 'error');
        console.error(err);
    }
}

// ============================================================
// CARREGAR PERFIL DO SERVIDOR
// Sincroniza todos os campos do formulário + histórico + foto
// ============================================================
async function carregarPerfilDoServidor(googleId) {
    if (!googleId) return;
    try {
        const res = await fetch(`${API}/get-profile/${googleId}`);
        if (!res.ok) return;
        const data = await res.json();

        // Preenche campos do formulário com dados do banco
        const campos = { nick: 'nick', bio: 'bio', telefone: 'telefone', cpf: 'cpf', data_nasc: 'dataNascimento' };
        Object.entries(campos).forEach(([chave, id]) => {
            const el = document.getElementById(id);
            if (el && data[chave]) el.value = data[chave];
        });

        // Atualiza objeto de estado local
        userProfile.nick           = data.nick          || '';
        userProfile.bio            = data.bio           || '';
        userProfile.telefone       = data.telefone      || '';
        userProfile.cpf            = data.cpf           || '';
        userProfile.dataNascimento = data.data_nasc     || '';

        // Atualiza nick no header se estiver salvo no banco
        if (data.nick) {
            document.getElementById('userName').textContent = data.nick;
        }

        // ── BUG CORRIGIDO: foto_path era um caminho do servidor (ex: "uploads/123.jpg")
        //    que o front tentava usar direto como src de <img> — isso não funciona.
        //    Agora o backend retorna foto_url (http://localhost:6500/uploads/...)
        //    graças ao StaticFiles montado no servidor.
        if (data.foto_url) {
            document.getElementById('previewFoto').src = data.foto_url;
            document.getElementById('userPicture').src = data.foto_url;
        }

        // Popula histórico com dados reais do banco
        historicoJogos = (data.historico || []).map(h => ({
            tipo:      h.tipo,
            resultado: h.resultado,
            valor:     h.valor,
            data:      new Date(h.data).toLocaleDateString('pt-BR')
        }));
        renderHistorico();

    } catch (e) {
        console.warn('Não foi possível carregar o perfil:', e);
    }
}

function executarLogout() {
    google.accounts.id.disableAutoSelect();
    sessionStorage.removeItem('dr_credential');

    userProfile = {
        googleId: null, nick: "", bio: "", telefone: "", cpf: "", dataNascimento: "",
        privacidade: { telefone: false, cpf: false }
    };
    historicoJogos = [];

    ['nick','bio','telefone','cpf','dataNascimento'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    document.getElementById('inputFoto').value   = '';
    document.getElementById('previewFoto').src   = 'https://ui-avatars.com/api/?name=User&background=random';
    document.getElementById('googleBtnContainer').style.display = 'block';
    document.getElementById('userInfo').style.display           = 'none';
    renderHistorico();
    showToast('Sessão encerrada.', 'info', 2000);
}

// ============================================================
// LÓGICA DE PERFIL
// ============================================================
function validarCPF(cpf) {
    cpf = cpf.replace(/[^\d]+/g, '');
    if (cpf.length !== 11 || /^(\d)\1{10}$/.test(cpf)) return false;
    let soma = 0, resto;
    for (let i = 1; i <= 9; i++) soma += parseInt(cpf[i-1]) * (11 - i);
    resto = (soma * 10) % 11;
    if (resto === 10 || resto === 11) resto = 0;
    if (resto !== parseInt(cpf[9])) return false;
    soma = 0;
    for (let i = 1; i <= 10; i++) soma += parseInt(cpf[i-1]) * (12 - i);
    resto = (soma * 10) % 11;
    if (resto === 10 || resto === 11) resto = 0;
    return resto === parseInt(cpf[10]);
}

function renderHistorico() {
    const tbody = document.getElementById('tabela-historico');
    if (!tbody) return;
    if (historicoJogos.length === 0) {
        tbody.innerHTML = '<tr><td colspan="2" style="padding:10px;text-align:center;color:var(--text-muted);font-style:italic;">Nenhuma partida registrada.</td></tr>';
        return;
    }
    tbody.innerHTML = historicoJogos.map(j => `
        <tr style="border-bottom:1px solid rgba(255,255,255,0.05);">
            <td style="padding:10px;text-transform:capitalize;">${j.tipo}</td>
            <td style="padding:10px;color:${j.resultado === 'vitoria' ? 'var(--accent-green)' : j.resultado === 'empate' ? 'var(--text-secondary)' : 'var(--accent-red)'}">${j.resultado}</td>
        </tr>
    `).join('');
}

function verificarIdade(dataNasc) {
    if (!dataNasc) return false;
    const hoje = new Date(), nasc = new Date(dataNasc);
    let idade = hoje.getFullYear() - nasc.getFullYear();
    if (hoje.getMonth() - nasc.getMonth() < 0 || (hoje.getMonth() === nasc.getMonth() && hoje.getDate() < nasc.getDate()))
        idade--;
    return idade >= 18;
}

function togglePrivacy(campo) {
    userProfile.privacidade[campo] = !userProfile.privacidade[campo];
    showToast(`${campo} agora está ${userProfile.privacidade[campo] ? 'Público' : 'Privado'}`, 'info');
}

function previewImagem(event) {
    const reader = new FileReader();
    reader.onload = () => { document.getElementById('previewFoto').src = reader.result; };
    if (event.target.files[0]) reader.readAsDataURL(event.target.files[0]);
}

async function salvarPerfil(event) {
    if (event) event.preventDefault();

    if (!userProfile.googleId) {
        showToast('Você precisa estar logado para salvar o perfil.', 'error');
        return;
    }

    const dataNasc = document.getElementById('dataNascimento').value;
    if (dataNasc && !verificarIdade(dataNasc)) {
        showToast('Você precisa ter 18 anos ou mais.', 'error');
        return;
    }

    const cpfValor = document.getElementById('cpf').value;
    if (cpfValor && !validarCPF(cpfValor)) {
        showToast('CPF inválido. Verifique os dígitos.', 'error');
        return;
    }

    // Atualiza estado local
    userProfile.nick           = document.getElementById('nick').value;
    userProfile.bio            = document.getElementById('bio').value;
    userProfile.telefone       = document.getElementById('telefone').value;
    userProfile.cpf            = cpfValor;
    userProfile.dataNascimento = dataNasc;

    // Feedback visual: desabilita botão durante o envio
    const btn = document.querySelector('#perfil-container .btn-primary');
    if (btn) { btn.disabled = true; btn.textContent = 'Salvando...'; }

    const formData = new FormData();
    const fileInput = document.getElementById('inputFoto');
    if (fileInput.files.length > 0)
        formData.append('foto', fileInput.files[0]);
    formData.append('googleId', userProfile.googleId);
    formData.append('dados', JSON.stringify(userProfile));

    try {
        const res = await fetch(`${API}/update-profile`, { method: 'POST', body: formData });
        if (res.ok) {
            const data = await res.json();
            showToast('Perfil atualizado com sucesso!', 'success');

            // Atualiza nick no header imediatamente
            if (userProfile.nick) {
                document.getElementById('userName').textContent = userProfile.nick;
            }

            // Atualiza foto no header se o servidor devolveu URL
            if (data.foto_url) {
                document.getElementById('userPicture').src = data.foto_url;
            }
        } else {
            const err = await res.json();
            showToast(`Erro: ${err.detail}`, 'error');
        }
    } catch (e) {
        console.error(e);
        showToast('Não foi possível salvar o perfil.', 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Salvar Perfil'; }
    }
}

// ============================================================
// INICIALIZAÇÃO
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
    inicializarBotaoGoogle();
});