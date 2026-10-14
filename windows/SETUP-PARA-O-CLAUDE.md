# Guia de instalação no Windows (para o Claude que vai fazer o setup)

Você é o Claude rodando no Windows do Gabriel, e ele pediu que **você** instale e configure tudo isto, sem ele ajustar nada na mão. Este guia diz o que fazer, como conferir e onde o risco está.

## O que é

Uma ferramenta que lê em voz alta as respostas do Claude Code (e, se existirem, Codex e Cursor), com a voz do Gemini TTS, mais um ícone na bandeja para controlar. Ela já roda no Mac do Gabriel e foi portada para o Windows **sem nunca ter sido executada em Windows**: o código foi escrito e revisado no Mac, e só a lógica independente de sistema foi testada lá. **Você é quem vai descobrir o que quebra de verdade.** Esperamos pequenos ajustes.

Também há duas coisas separadas para instalar: a ferramenta de voz (pasta `tts`) e as skills do Claude Code (pasta `skills-windows`), que só existem no pacote que o Gabriel te entregou. Se a pasta `skills-windows` não estiver ao lado deste guia, instale só a ferramenta e avise o Gabriel de que as skills ficaram de fora.

## Regras para você

- Pode e deve **corrigir o código** da ferramenta se algo falhar no Windows. Mantenha o macOS funcionando: o mesmo código roda nos dois, e as diferenças ficam em `plataforma.py` e `win_integracao.py`.
- **Nunca mostre, imprima nem registre a chave do Gemini.** Ela só entra pelo comando `tts chave gemini`, que lê a área de transferência e guarda no Gerenciador de Credenciais.
- Não mexa em hooks, skills ou regras de outras ferramentas além do que está aqui.
- Antes de rodar o teste de áudio, avise o Gabriel de que vai tocar som por uns 15 segundos.
- No fim, entregue um resumo curto do que funcionou, do que você precisou corrigir (com o diff dos arquivos alterados) e do que ficou pendente.

## Passo a passo

1. **Pré-requisitos.** Python 3.10 ou mais novo, `ffmpeg` no PATH e Git for Windows (os hooks do Claude Code rodam no bash dele). Se faltar algo: `winget install Python.Python.3.12`, `winget install Gyan.FFmpeg`, `winget install Git.Git`. O instalador já tenta o ffmpeg sozinho.

2. **Instalar a ferramenta.** Na pasta `tts`:
   ```powershell
   powershell -ExecutionPolicy Bypass -File windows\install.ps1
   ```
   Ele copia os arquivos para `%USERPROFILE%\.tts-agentes`, cria o ambiente Python, cria o comando `tts`, configura os hooks no `%USERPROFILE%\.claude\settings.json` e põe o ícone na bandeja para abrir no login. Se o `ffmpeg` recém-instalado não for reconhecido, abra um novo terminal e rode de novo.

3. **Instalar as skills** (se `skills-windows` existir). Nessa pasta:
   ```powershell
   powershell -ExecutionPolicy Bypass -File instalar.ps1
   ```
   Isso copia as quatro skills (`speak-work`, `speak-personal`, `read-work`, `read-personal`) para `%USERPROFILE%\.claude\skills` e acrescenta o bloco da regra global ao `CLAUDE.md`, sem apagar o resto.

4. **Chave do Gemini.** Peça ao Gabriel para criar ou copiar a chave em https://aistudio.google.com/apikey (o projeto precisa de faturamento ativo; o plano grátis só permite 10 pedidos por dia), copiá-la para a área de transferência e te avisar. Então rode `tts chave gemini`. Confira só a mensagem de sucesso, que mostra 4 caracteres e o tamanho. Sem a chave a ferramenta usa a voz grátis da Microsoft, então dá para validar o resto antes.

## Conferência (faça tudo e reporte)

1. `tts diagnostico`: ffmpeg, ffprobe, saída de áudio, edge-tts, chave, hooks e dependências. Tudo precisa estar ✅.
2. `tts autoteste`: grava três falas em arquivo, procura silêncios no meio e depois toca três falas ao vivo conferindo que terminam sem erro. Toca som.
3. **Ícone na bandeja.** Procure o ícone circular perto do relógio (talvez em "ícones ocultos"). Confira que o menu abre com Voz, Velocidade e Modelo, e que **Leitura ligada** começa desmarcada (a ferramenta sempre abre desligada).
4. **Hook do Claude Code.** Ligue a leitura, peça qualquer coisa curta numa conversa do Claude Code e confira em `tts log` uma linha `hook origem=claude`. Se o log não mostrar nada, o hook não rodou: veja o comando em `%USERPROFILE%\.claude\settings.json` e rode-o à mão no terminal do Git Bash e no PowerShell para ver o erro. Lembre que os hooks usam barras normais e aspas nos caminhos.
5. **Atalhos globais.** `Ctrl+Alt+P` pausa e continua a fala; `Ctrl+Alt+S` lê o texto selecionado. Se `tts log` disser que um atalho está indisponível, outro programa o usa: mude `atalho_pausa` ou `atalho_selecao` em `%USERPROFILE%\.tts-agentes\config.json`. Teclado ABNT2: `Ctrl+Alt` equivale ao AltGr, então prefira combinações que não digitem símbolos.
6. **Pausa e retomada.** Durante uma fala longa (`tts dizer "frase longa..."`), pause e continue pelo atalho e pelo menu; deve voltar do ponto exato, sem repetir nem pular.
7. **Falar seleção** num app comum (Bloco de Notas ou o navegador): seleciona, atalho, ouve; a área de transferência volta ao que era.

## Onde é mais provável quebrar

| Área | Sintoma | Onde olhar |
|---|---|---|
| Hook não dispara | `tts log` vazio depois de uma resposta | comando no `settings.json`; Git Bash no PATH; barras e aspas |
| Console piscando | janela preta aparece a cada fala | `plataforma.sem_janela()` e as flags em `iniciar_destacado` |
| Sem som ou som estranho | `diagnostico` mostra o dispositivo errado | `sounddevice` usa a saída padrão do Windows |
| Voz offline falha | `tts` cai no silêncio sem Gemini nem Microsoft | `plataforma.voz_offline_pcm` usa o SAPI; a voz padrão `Microsoft Maria Desktop` exige o idioma pt-BR instalado; veja as vozes em `Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices()` e ajuste `voz_offline` no `config.json` |
| Não para ou não pausa | `Parar fala` não corta o som | `plataforma.encerrar_arvore` e a trava do worker (`TravaArquivo`, com `msvcrt`) |
| Atalho não funciona | nada acontece ao apertar | `win_integracao.registrar_atalhos` e conflitos de combinação |
| Falar seleção vazio | "nada selecionado" | `win_integracao.texto_selecionado` (Ctrl+C simulado); apps com permissões elevadas não aceitam entrada simulada |
| Chave não é lida | `tts diagnostico` mostra ❌ na chave depois de salvar | `keyring` e o Gerenciador de Credenciais; o serviço é `tts-agentes`, a conta é `tts-gemini` |
| Acentos quebrados | `?` ou caracteres estranhos no log ou nas falas | tudo usa UTF-8 explícito; confira `plataforma.preparar_console` |

## Como o código está organizado

- `tts.py`: o núcleo, igual nos dois sistemas. Fila de falas, geração de áudio por streaming do Gemini, voz reserva da Microsoft e voz do sistema, hooks, "Falar agora", "Falar seleção".
- `plataforma.py`: tudo o que muda entre Mac e Windows (cofre de chaves, trava de arquivo, processos, voz offline, caminhos).
- `win_integracao.py`: área de transferência, teclas simuladas e atalhos globais, só com `ctypes`.
- `tray_windows.py`: o ícone da bandeja e o menu (no Mac é o `menubar.py`).
- `configurar_hooks.py`: instala e remove os hooks.
- `autoteste.py`: o teste de áudio.

## No Windows, o que é diferente do Mac

| | Mac | Windows |
|---|---|---|
| Chave | Keychain de login | Gerenciador de Credenciais |
| Voz offline | `say` (Luciana) | SAPI (Microsoft Maria Desktop) |
| Pausa | ⌥P | Ctrl+Alt+P |
| Falar seleção | ⌥Esc | Ctrl+Alt+S |
| Ícone | barra de menus | bandeja do sistema |
| Abre no login | LaunchAgent | atalho na pasta Inicializar |
| Teste de app iOS | Simulador + Maestro | não existe: só o navegador |

## Se precisar desfazer

`powershell -ExecutionPolicy Bypass -File windows\uninstall.ps1` remove a ferramenta, os hooks e o ícone. A chave no Gerenciador de Credenciais fica.
