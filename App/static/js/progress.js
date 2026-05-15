(function () {
  const panel = document.getElementById('progress-panel');
  const title = document.getElementById('progress-title');
  const percent = document.getElementById('progress-percent');
  const fill = document.getElementById('progress-fill');
  const message = document.getElementById('progress-message');
  const detail = document.getElementById('progress-detail');

  if (!panel) return;

  let pollTimer = null;

  const TITLES = {
    generate_transcription: 'Gerar transcrição',
    suggest_cuts: 'Sugerir cortes com IA',
    process_cuts: 'Processar cortes'
  };

  function setState(state) {
    panel.classList.remove('is-running', 'is-success', 'is-error');
    if (state === 'running') panel.classList.add('is-running');
    if (state === 'success') panel.classList.add('is-success');
    if (state === 'error') panel.classList.add('is-error');
  }

  function updateBar(status, jobType) {
    panel.hidden = false;
    title.textContent = TITLES[jobType] || 'Processando';

    const value = Number(status.progress || 0);
    percent.textContent = `${value}%`;
    fill.style.width = `${Math.max(0, Math.min(100, value))}%`;

    message.textContent = status.message || 'Processando...';
    detail.textContent = status.detail || '';

    setState(status.state || 'running');
  }

  function stopPolling() {
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  async function pollStatus(projectId, jobType) {
    try {
      const response = await fetch(`/job_status/${projectId}/${jobType}`);
      const status = await response.json();

      updateBar(status, jobType);

      if (status.state === 'running') {
        pollTimer = setTimeout(() => pollStatus(projectId, jobType), 1200);
        return;
      }

      stopPolling();

      if (status.state === 'success' || status.state === 'error') {
        setTimeout(() => window.location.reload(), 1200);
      }
    } catch (error) {
      updateBar(
        {
          state: 'error',
          progress: 100,
          message: 'Erro ao consultar status',
          detail: String(error)
        },
        jobType
      );
      stopPolling();
    }
  }

  async function startJob(form) {
    const jobType = form.dataset.jobType;
    const projectId = form.dataset.projectId;

    if (!jobType || !projectId) return;

    stopPolling();
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
        return;
      }

      pollStatus(projectId, jobType);
    } catch (error) {
      updateBar(
        {
          state: 'error',
          progress: 100,
          message: 'Erro ao iniciar processamento',
          detail: String(error)
        },
        jobType
      );
    }
  }

  document.querySelectorAll('form[data-progress-job="true"]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      startJob(form);
    });
  });
})();