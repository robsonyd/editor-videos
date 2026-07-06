(function () {
  const panel = document.getElementById('progress-panel');
  const title = document.getElementById('progress-title');
  const stateLabel = document.getElementById('progress-state-label');
  const percent = document.getElementById('progress-percent');
  const fill = document.getElementById('progress-fill');
  const message = document.getElementById('progress-message');
  const detail = document.getElementById('progress-detail');
  const elapsed = document.getElementById('progress-elapsed');
  const estimate = document.getElementById('progress-estimate');
  const updated = document.getElementById('progress-updated');
  const hint = document.getElementById('progress-hint');
  const mode = document.getElementById('progress-mode');
  const cancelButton = document.getElementById('progress-cancel-button');
  const actionHint = document.getElementById('progress-action-hint');
  const magicGif = document.getElementById('progress-magic-gif');
  const magicPercent = document.getElementById('progress-magic-percent');

  if (!panel) return;

  let pollTimer = null;
  let elapsedTimer = null;
  let localStartedAt = null;
  let lastProgress = null;
  let lastProgressChangedAt = null;
  let activeJob = null;
  let latestStatus = null;
  let allowUnloadWithoutCancel = false;
  let cancelRequested = false;

  const STORAGE_KEY = 'evr_active_progress_job';

  const TITLES = {
    generate_transcription: 'Gerar transcrição',
    identify_speakers: 'Mapear participantes',
    suggest_cuts: 'Sugerir cortes com IA',
    process_cuts: 'Processar cortes',
    split_video: 'Video Splitter'
  };

  const LONG_RUNNING_HINTS = {
    generate_transcription: 'Whisper, revisão por IA e arquivos SRT podem levar alguns minutos em vídeos longos.',
    identify_speakers: 'Mapeamento de participantes é pesado. O percentual pode ficar parado por alguns minutos sem significar travamento.',
    suggest_cuts: 'A IA está analisando a transcrição e pode demorar mais em episódios longos.',
    process_cuts: 'O FFmpeg está renderizando os arquivos selecionados. Evite fechar esta aba durante a saída dos vídeos.',
    split_video: 'O FFmpeg está gerando as partes do vídeo. O tempo varia conforme duração e quantidade de trechos.'
  };

  function now() {
    return Date.now();
  }

  function formatDuration(ms) {
    const totalSeconds = Math.max(0, Math.floor(ms / 1000));
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;

    if (hours > 0) {
      return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
    }

    return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  }

  function secondsToText(seconds) {
    return formatDuration(Number(seconds || 0) * 1000);
  }

  function getCurrentElapsedSeconds() {
    if (latestStatus && latestStatus.state === 'running') {
      const startedElapsed = Number(latestStatus.runtime_elapsed_seconds ?? latestStatus.elapsed_seconds ?? 0);
      const updatedAt = Number(latestStatus._receivedAt || now());
      return Math.max(0, startedElapsed + Math.floor((now() - updatedAt) / 1000));
    }

    if (latestStatus && latestStatus.elapsed_seconds !== undefined) {
      return Math.max(0, Number(latestStatus.elapsed_seconds || 0));
    }

    if (!localStartedAt) return 0;
    return Math.floor((now() - localStartedAt) / 1000);
  }

  function getElapsedText() {
    return secondsToText(getCurrentElapsedSeconds());
  }

  function showProgressLock() {
    panel.hidden = false;
    document.body.classList.add('progress-lock');
  }

  function hideProgressLock() {
    panel.hidden = true;
    document.body.classList.remove('progress-lock');
  }

  function setMagicVisual(options = {}) {
    const isMagic = Boolean(options.magic);
    panel.classList.toggle('is-magic', isMagic);
    if (isMagic && magicGif && options.gif) {
      magicGif.src = options.gif;
    }
    if (!isMagic && magicGif) {
      magicGif.removeAttribute('src');
    }
  }

  function setState(state) {
    panel.classList.remove('is-running', 'is-success', 'is-error');
    if (state === 'success') panel.classList.add('is-success');
    else if (state === 'error') panel.classList.add('is-error');
    else panel.classList.add('is-running');

    if (!stateLabel) return;
    if (state === 'success') stateLabel.textContent = 'Concluído';
    else if (state === 'error') stateLabel.textContent = 'Falhou';
    else stateLabel.textContent = 'Processando';
  }

  function getDynamicEstimateText() {
    if (!latestStatus) return 'Calculando';
    if (latestStatus.state === 'success') return 'Concluído';
    if (latestStatus.state === 'error') return 'Falhou';

    const elapsedSeconds = getCurrentElapsedSeconds();
    const estimatedTotal = Number(latestStatus.estimated_total_seconds || 0);
    if (estimatedTotal > 0) {
      return `~${secondsToText(Math.max(0, estimatedTotal - elapsedSeconds))}`;
    }

    const progress = Number(latestStatus.progress || 0);
    if (progress >= 5 && elapsedSeconds > 0) {
      const projectedTotal = Math.max(elapsedSeconds, elapsedSeconds / Math.max(progress / 100, 0.01));
      return `~${secondsToText(Math.max(0, projectedTotal - elapsedSeconds))}`;
    }

    const sampleCount = Number(latestStatus.history_sample_count || 0);
    if (sampleCount < 2) return 'Aprendendo';
    return 'Calculando';
  }

  function getUpdatedText() {
    if (!latestStatus) return 'Iniciando';
    if (latestStatus.state === 'success') return 'Finalizado';
    if (latestStatus.state === 'error') return 'Erro';

    const updatedAgo = Number(latestStatus.updated_ago_seconds || 0);
    const receivedAgo = Math.floor((now() - Number(latestStatus._receivedAt || now())) / 1000);
    const totalAgo = Math.max(0, updatedAgo + receivedAgo);
    if (totalAgo < 5) return 'Agora';
    return `${secondsToText(totalAgo)} atrás`;
  }

  function buildHintText() {
    if (!activeJob) return '';

    const baseHint = LONG_RUNNING_HINTS[activeJob.jobType] || 'Processamento em andamento.';
    if (!latestStatus || latestStatus.state !== 'running') return baseHint;

    const hints = [baseHint];
    const sampleCount = Number(latestStatus.history_sample_count || 0);
    const source = latestStatus.estimate_source || '';
    const unchangedSeconds = lastProgressChangedAt ? Math.floor((now() - lastProgressChangedAt) / 1000) : 0;

    if (source === 'history' || source === 'history_and_progress') {
      hints.push('Previsão baseada no histórico local deste computador.');
    } else if (sampleCount < 2) {
      hints.push('Depois de alguns processamentos, o EVR passa a estimar melhor o tempo restante.');
    }

    if (unchangedSeconds >= 90) {
      hints.push(`Sem mudança de percentual há ${secondsToText(unchangedSeconds)}, mas o processo pode continuar ativo.`);
    }

    return hints.join(' ');
  }

  function updateLiveMetrics() {
    if (elapsed) elapsed.textContent = getElapsedText();
    if (estimate) estimate.textContent = getDynamicEstimateText();
    if (updated) updated.textContent = getUpdatedText();
    if (hint) hint.textContent = buildHintText();
  }

  function setCancelButtonState() {
    if (!cancelButton) return;

    if (latestStatus && latestStatus.state === 'error') {
      cancelButton.disabled = false;
      cancelButton.textContent = 'Concluir';
      if (actionHint) actionHint.textContent = 'Leia o erro antes de voltar para o projeto.';
      return;
    }

    const isRunning = latestStatus && latestStatus.state === 'running';
    cancelButton.disabled = !isRunning || cancelRequested;
    cancelButton.textContent = cancelRequested ? 'Interrompendo...' : 'Parar processamento';
    if (actionHint) actionHint.textContent = 'Interrompe o job atual e libera o EVR Deluxe.';
  }

  function startElapsedTimer() {
    if (elapsedTimer) clearInterval(elapsedTimer);
    updateLiveMetrics();
    elapsedTimer = setInterval(updateLiveMetrics, 1000);
  }

  function stopElapsedTimer() {
    if (elapsedTimer) {
      clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
  }

  function saveActiveJob(projectId, jobType, startedAt) {
    activeJob = { projectId, jobType, startedAt };
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(activeJob));
  }

  function clearActiveJob() {
    activeJob = null;
    cancelRequested = false;
    sessionStorage.removeItem(STORAGE_KEY);
    setCancelButtonState();
  }

  function cancelActiveJobOnUnload() {
    if (!activeJob || allowUnloadWithoutCancel) return;

    const { projectId, jobType } = activeJob;
    if (!projectId || !jobType) return;

    const url = `/cancel_job/${encodeURIComponent(projectId)}/${encodeURIComponent(jobType)}`;
    const payload = JSON.stringify({ reason: 'page_unload' });

    if (navigator.sendBeacon) {
      const body = new Blob([payload], { type: 'application/json' });
      navigator.sendBeacon(url, body);
    } else {
      fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: payload,
        keepalive: true
      }).catch(() => {});
    }
  }

  function loadActiveJob() {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed || !parsed.projectId || !parsed.jobType || !parsed.startedAt) return null;
      return parsed;
    } catch (_) {
      return null;
    }
  }

  function updateBar(status, jobType) {
    showProgressLock();
    latestStatus = { ...(status || {}), _receivedAt: now() };
    if (title) title.textContent = TITLES[jobType] || 'Processando';
    if (mode) {
      mode.textContent = latestStatus.magic_mode
        ? 'Botão Mágico ativo'
        : latestStatus.performance_label
          ? `Modo aplicado: ${latestStatus.performance_label}`
          : 'EVR Deluxe';
    }

    const value = Number(latestStatus.progress || 0);
    if (lastProgress === null || value !== lastProgress) {
      lastProgress = value;
      lastProgressChangedAt = now();
    }

    if (percent) percent.textContent = `${value}%`;
    if (magicPercent) magicPercent.textContent = `${value}%`;
    if (fill) fill.style.width = `${Math.max(0, Math.min(100, value))}%`;

    if (message) message.textContent = latestStatus.message || 'Processando...';
    if (detail) detail.textContent = latestStatus.detail || '';

    setState(latestStatus.state || 'running');
    setMagicVisual({ magic: Boolean(latestStatus.magic_mode), gif: latestStatus.magic_gif || '' });
    updateLiveMetrics();
    setCancelButtonState();
  }

  function setExternalProgressState(jobType, status = {}, options = {}) {
    if (!localStartedAt || !elapsedTimer) {
      localStartedAt = now();
      lastProgressChangedAt = localStartedAt;
      startElapsedTimer();
    }

    latestStatus = {
      state: status.state || 'running',
      progress: Number(status.progress || 0),
      message: status.message || 'Processando...',
      detail: status.detail || '',
      performance_label: status.performance_label || '',
      magic_mode: Boolean(options.magic),
      magic_gif: options.gif || '',
      _receivedAt: now()
    };
    updateBar(latestStatus, jobType);

    if (latestStatus.state !== 'running') {
      stopElapsedTimer();
    }
  }

  function trackExternalJob(projectId, jobType) {
    if (!projectId || !jobType) return;
    cancelRequested = false;
    const startedAt = localStartedAt || now();
    localStartedAt = startedAt;
    saveActiveJob(projectId, jobType, startedAt);
    startElapsedTimer();
    setCancelButtonState();
  }

  function clearExternalJob() {
    clearActiveJob();
  }

  function stopPolling() {
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  function holdErrorUntilAcknowledged() {
    stopPolling();
    stopElapsedTimer();
    clearActiveJob();
    setCancelButtonState();
  }

  function acknowledgeErrorAndReload() {
    allowUnloadWithoutCancel = true;
    stopPolling();
    stopElapsedTimer();
    clearActiveJob();
    window.location.reload();
  }

  async function pollStatus(projectId, jobType) {
    try {
      const response = await fetch(`/job_status/${projectId}/${jobType}`, {
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
      });

      const contentType = response.headers.get('content-type') || '';
      if (!contentType.includes('application/json')) {
        const raw = await response.text();
        throw new Error(`Status inválido: ${raw.slice(0, 200)}`);
      }

      const status = await response.json();
      updateBar(status, jobType);

      if (status.state === 'running') {
        pollTimer = setTimeout(() => pollStatus(projectId, jobType), 1200);
        return;
      }

      stopPolling();
      stopElapsedTimer();
      clearActiveJob();

      if (status.state === 'success') {
        sessionStorage.setItem(
          'evr_completed_progress_job',
          JSON.stringify({ projectId, jobType, finishedAt: Date.now() })
        );
        setTimeout(() => {
          allowUnloadWithoutCancel = true;
          window.location.reload();
        }, 1300);
        return;
      }

      if (status.state === 'error') {
        holdErrorUntilAcknowledged();
      }
    } catch (error) {
      updateBar(
        {
          state: 'error',
          progress: 100,
          message: 'Erro ao consultar status',
          detail: String(error.message || error)
        },
        jobType
      );
      holdErrorUntilAcknowledged();
    }
  }

  async function cancelActiveJobFromButton() {
    if (!activeJob || cancelRequested) return;

    const confirmed = window.confirm(
      'Parar o processamento atual?\n\nO EVR Deluxe vai interromper o job em andamento. Arquivos parciais podem ficar na pasta do projeto e esta etapa precisará ser executada novamente.'
    );
    if (!confirmed) return;

    const { projectId, jobType } = activeJob;
    cancelRequested = true;
    setCancelButtonState();

    if (message) message.textContent = 'Interrompendo processamento...';
    if (detail) detail.textContent = 'Encerrando subprocessos e salvando o estado do projeto.';
    if (updated) updated.textContent = 'Parando';

    try {
      const response = await fetch(`/cancel_job/${encodeURIComponent(projectId)}/${encodeURIComponent(jobType)}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Requested-With': 'XMLHttpRequest'
        },
        body: JSON.stringify({ reason: 'manual_stop' })
      });

      const contentType = response.headers.get('content-type') || '';
      if (!contentType.includes('application/json')) {
        const raw = await response.text();
        throw new Error(`Resposta inesperada do servidor: ${raw.slice(0, 200)}`);
      }

      const data = await response.json();
      if (!response.ok || !data.ok) {
        throw new Error(data.message || 'Não foi possível interromper o processamento.');
      }

      stopPolling();
      pollStatus(projectId, jobType);
    } catch (error) {
      cancelRequested = false;
      setCancelButtonState();
      if (message) message.textContent = 'Não foi possível parar agora';
      if (detail) detail.textContent = String(error.message || error);
    }
  }

  async function startJob(form) {
    const jobType = form.dataset.jobType;
    const projectId = form.dataset.projectId;

    if (!jobType || !projectId) return;

    const startedAt = now();
    localStartedAt = startedAt;
    lastProgress = null;
    lastProgressChangedAt = startedAt;
    saveActiveJob(projectId, jobType, startedAt);

    stopPolling();
    startElapsedTimer();
    updateBar(
      {
        state: 'running',
        progress: 2,
        message: 'Iniciando...',
        detail: ''
      },
      jobType
    );

    try {
      const response = await fetch(form.action, {
        method: 'POST',
        headers: {
          'X-Requested-With': 'XMLHttpRequest'
        },
        body: new FormData(form)
      });

      const contentType = response.headers.get('content-type') || '';
      if (!contentType.includes('application/json')) {
        const raw = await response.text();
        throw new Error(`Resposta inesperada do servidor: ${raw.slice(0, 200)}`);
      }

      const data = await response.json();

      if (!response.ok || !data.ok) {
        updateBar(
          {
            state: 'error',
            progress: 100,
            message: data.message || 'Não foi possível iniciar o processamento.',
            detail: data.detail || ''
          },
          jobType
        );
        holdErrorUntilAcknowledged();
        return;
      }

      pollStatus(projectId, jobType);
    } catch (error) {
      updateBar(
        {
          state: 'error',
          progress: 100,
          message: 'Erro ao iniciar processamento',
          detail: String(error.message || error)
        },
        jobType
      );
      holdErrorUntilAcknowledged();
    }
  }

  document.querySelectorAll('form[data-progress-job="true"]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      startJob(form);
    });
  });

  if (cancelButton) {
    cancelButton.addEventListener('click', () => {
      if (latestStatus && latestStatus.state === 'error') {
        acknowledgeErrorAndReload();
        return;
      }
      cancelActiveJobFromButton();
    });
  }

  window.EVRProgress = {
    setState: setExternalProgressState,
    trackJob: trackExternalJob,
    clearJob: clearExternalJob,
    setMagic: (gif) => setMagicVisual({ magic: true, gif }),
    clearMagic: () => setMagicVisual({ magic: false })
  };

  const storedJob = loadActiveJob();
  if (storedJob) {
    localStartedAt = Number(storedJob.startedAt) || now();
    lastProgressChangedAt = localStartedAt;
    activeJob = storedJob;
    startElapsedTimer();
    updateBar(
      {
        state: 'running',
        progress: 1,
        message: 'Retomando acompanhamento...',
        detail: ''
      },
      storedJob.jobType
    );
    pollStatus(storedJob.projectId, storedJob.jobType);
  }

  window.addEventListener('pagehide', cancelActiveJobOnUnload);
})();
