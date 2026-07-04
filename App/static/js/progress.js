(function () {
  const panel = document.getElementById('progress-panel');
  const title = document.getElementById('progress-title');
  const percent = document.getElementById('progress-percent');
  const fill = document.getElementById('progress-fill');
  const message = document.getElementById('progress-message');
  const detail = document.getElementById('progress-detail');
  const elapsed = document.getElementById('progress-elapsed');
  const hint = document.getElementById('progress-hint');

  if (!panel) return;

  let pollTimer = null;
  let elapsedTimer = null;
  let localStartedAt = null;
  let lastProgress = null;
  let lastProgressChangedAt = null;
  let activeJob = null;

  const STORAGE_KEY = 'evr_active_progress_job';

  const TITLES = {
    generate_transcription: 'Gerar transcrição',
    identify_speakers: 'Identificar speakers',
    suggest_cuts: 'Sugerir cortes com IA',
    process_cuts: 'Processar cortes',
    split_video: 'Video Splitter'
  };

  const LONG_RUNNING_HINTS = {
    generate_transcription: 'Transcrição pode levar alguns minutos em vídeos longos.',
    identify_speakers: 'Diarização é uma etapa pesada. Em podcasts longos, pode demorar bastante e a porcentagem pode ficar parada por alguns minutos.',
    suggest_cuts: 'A IA está analisando o conteúdo. Aguarde a resposta.',
    process_cuts: 'O FFmpeg está renderizando os arquivos selecionados.',
    split_video: 'O FFmpeg está gerando as partes do vídeo.'
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

  function getElapsedText() {
    if (!localStartedAt) return '00:00';
    return formatDuration(now() - localStartedAt);
  }

  function setState(state) {
    panel.classList.remove('is-running', 'is-success', 'is-error');
    if (state === 'success') panel.classList.add('is-success');
    else if (state === 'error') panel.classList.add('is-error');
    else panel.classList.add('is-running');
  }

  function updateElapsedOnly() {
    if (elapsed) elapsed.textContent = getElapsedText();

    if (activeJob && hint && panel.classList.contains('is-running')) {
      const baseHint = LONG_RUNNING_HINTS[activeJob.jobType] || 'Processamento em andamento.';
      const unchangedSeconds = lastProgressChangedAt ? Math.floor((now() - lastProgressChangedAt) / 1000) : 0;

      if (unchangedSeconds >= 90) {
        hint.textContent = `${baseHint} Sem mudança de percentual há ${formatDuration(unchangedSeconds * 1000)}, mas o processo pode continuar ativo.`;
      } else {
        hint.textContent = baseHint;
      }
    }
  }

  function startElapsedTimer() {
    if (elapsedTimer) clearInterval(elapsedTimer);
    updateElapsedOnly();
    elapsedTimer = setInterval(updateElapsedOnly, 1000);
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
    sessionStorage.removeItem(STORAGE_KEY);
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
    panel.hidden = false;
    if (title) title.textContent = TITLES[jobType] || 'Processando';

    const value = Number(status.progress || 0);
    if (lastProgress === null || value !== lastProgress) {
      lastProgress = value;
      lastProgressChangedAt = now();
    }

    if (percent) percent.textContent = `${value}%`;
    if (fill) fill.style.width = `${Math.max(0, Math.min(100, value))}%`;

    if (message) message.textContent = status.message || 'Processando...';
    if (detail) detail.textContent = status.detail || '';

    setState(status.state || 'running');
    updateElapsedOnly();
  }

  function stopPolling() {
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
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

      if (status.state === 'success' || status.state === 'error') {
        setTimeout(() => window.location.reload(), 1300);
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
      stopPolling();
      stopElapsedTimer();
      clearActiveJob();
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
        stopElapsedTimer();
        clearActiveJob();
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
      stopElapsedTimer();
      clearActiveJob();
    }
  }

  document.querySelectorAll('form[data-progress-job="true"]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      startJob(form);
    });
  });

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
})();
