(function () {
  function refreshChoiceState(input) {
    const card = input.closest('.radio-card, .mode-card, .speaker-checkbox');
    if (!card) return;

    if (input.type === 'radio') {
      document.querySelectorAll(`input[type="radio"][name="${input.name}"]`).forEach((peer) => {
        const peerCard = peer.closest('.radio-card, .mode-card');
        if (peerCard) peerCard.classList.toggle('is-selected', peer.checked);
      });
      return;
    }

    card.classList.toggle('is-selected', input.checked);
  }

  document.querySelectorAll('.radio-card input, .mode-card input, .speaker-checkbox input').forEach((input) => {
    refreshChoiceState(input);
    input.addEventListener('change', () => refreshChoiceState(input));
  });

  document.querySelectorAll('details.evr-collapse[data-persist-key]').forEach((detail) => {
    detail.addEventListener('toggle', () => {
      detail.classList.toggle('is-open', detail.open);
    });
  });

  document.querySelectorAll('button, .button, .nav-link, .settings-link, .back-link').forEach((element) => {
    element.addEventListener('pointerdown', () => {
      element.classList.add('is-pressing');
    });
    ['pointerup', 'pointercancel', 'mouseleave', 'blur'].forEach((eventName) => {
      element.addEventListener(eventName, () => {
        element.classList.remove('is-pressing');
      });
    });
  });

  const spotlightTargets = document.querySelectorAll(
    '.home-console, .settings-console, .box, .project-row, .settings-card, .speaker-card, .ai-card, .radio-card, .file-card, .technical-log-panel'
  );

  spotlightTargets.forEach((element) => {
    element.addEventListener('pointermove', (event) => {
      const rect = element.getBoundingClientRect();
      const x = ((event.clientX - rect.left) / rect.width) * 100;
      const y = ((event.clientY - rect.top) / rect.height) * 100;
      element.style.setProperty('--mx', `${x}%`);
      element.style.setProperty('--my', `${y}%`);
    });
  });

  document.querySelectorAll('.delete-choice, .duplicate-choice').forEach((detail) => {
    detail.addEventListener('toggle', () => {
      if (detail.open) {
        document.querySelectorAll('.delete-choice[open], .duplicate-choice[open]').forEach((otherDetail) => {
          if (otherDetail !== detail) otherDetail.open = false;
        });
      }

      const row = detail.closest('.project-row');
      if (row) row.classList.toggle('is-menu-open', detail.open);
    });
  });

  const projectFilters = document.querySelector('[data-project-filters]');
  if (projectFilters) {
    const rows = Array.from(document.querySelectorAll('[data-project-row]'));
    const searchInput = projectFilters.querySelector('[data-project-search]');
    const flowFilter = projectFilters.querySelector('[data-project-flow-filter]');
    const statusFilter = projectFilters.querySelector('[data-project-status-filter]');
    const showArchived = projectFilters.querySelector('[data-project-show-archived]');
    const emptyState = document.querySelector('[data-project-filter-empty]');

    function normalize(value) {
      return String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
    }

    function refreshProjectFilters() {
      const query = normalize(searchInput?.value || '');
      const flow = flowFilter?.value || '';
      const status = statusFilter?.value || '';
      const includeArchived = Boolean(showArchived?.checked) || status === 'archived';
      let visibleCount = 0;

      rows.forEach((row) => {
        const archived = row.dataset.archived === 'true';
        const haystack = normalize(`${row.dataset.title || ''} ${row.dataset.id || ''} ${row.dataset.video || ''} ${row.dataset.stage || ''}`);
        const matchesQuery = !query || haystack.includes(query);
        const matchesFlow = !flow || row.dataset.flow === flow;
        const matchesStatus = !status || row.dataset.status === status;
        const matchesArchiveVisibility = includeArchived || !archived;
        const isVisible = matchesQuery && matchesFlow && matchesStatus && matchesArchiveVisibility;
        row.hidden = !isVisible;
        if (isVisible) visibleCount += 1;
      });

      if (emptyState) emptyState.hidden = visibleCount !== 0;
    }

    [searchInput, flowFilter, statusFilter, showArchived].forEach((control) => {
      if (!control) return;
      control.addEventListener('input', refreshProjectFilters);
      control.addEventListener('change', refreshProjectFilters);
    });

    refreshProjectFilters();
  }

  function getTourSteps(scope) {
    const tours = {
      home: [
        {
          target: '[data-tour-target="api-alert"]',
          title: 'Conecte a inteligência artificial',
          body: 'OpenAI e Hugging Face precisam estar testadas e OK para revisar transcrições, mapear participantes e gerar cortes com IA.'
        },
        {
          target: '[data-tour-target="storage"]',
          title: 'Pasta de trabalho',
          body: 'Defina onde os projetos, transcrições, logs e arquivos gerados ficam salvos no seu computador.'
        },
        {
          target: '[data-tour-target="task"]',
          title: 'Escolha o fluxo',
          body: 'Comece escolhendo entre cortes inteligentes com I.A ou Video Splitter antes de enviar o vídeo bruto.'
        },
        {
          target: '[data-tour-target="upload"]',
          title: 'Vídeo bruto',
          body: 'Envie o arquivo original aqui. O EVR Deluxe copia esse vídeo para a pasta do projeto.'
        },
        {
          target: '[data-tour-target="projects"]',
          title: 'Projetos locais',
          body: 'Continue, abra a pasta, duplique, arquive ou remova projetos sem depender de nuvem.'
        },
        {
          target: '[data-tour-target="settings"]',
          title: 'Configurações',
          body: 'Chaves, tokens, glossário e modo de desempenho ficam reunidos aqui.'
        }
      ],
      settings: [
        {
          target: '[data-tour-target="settings-performance"]',
          title: 'Desempenho',
          body: 'Escolha o ritmo de processamento antes de trabalhos longos. Econômico prioriza conforto térmico; máximo prioriza velocidade.'
        },
        {
          target: '[data-tour-target="settings-glossary"]',
          title: 'Glossário',
          body: 'Cadastre termos que a IA deve corrigir automaticamente na transcrição revisada, como nomes de empresas e expressões recorrentes.'
        },
        {
          target: '[data-tour-target="settings-apis"]',
          title: 'APIs e testes',
          body: 'Cole as chaves, salve e teste OpenAI e Hugging Face. A Home só deixa de alertar quando os testes ficarem OK.'
        }
      ],
      ai_cuts: [
        {
          target: '[data-tour-target="ai-start"]',
          title: 'Comece por aqui',
          body: 'Este botão inicia a primeira etapa real: transcrever e revisar o vídeo para liberar o restante do fluxo.'
        },
        {
          target: '[data-tour-target="ai-automation"]',
          title: 'Automatizar',
          body: 'Use quando quiser escolher até onde o EVR roda sozinho e em qual etapa você assume manualmente.'
        },
        {
          target: '[data-tour-target="ai-reset"]',
          title: 'Limpar etapas',
          body: 'Remove resultados gerados neste projeto e permite refazer o fluxo sem apagar o vídeo bruto.'
        },
        {
          target: '[data-tour-target="ai-magic"]',
          title: 'Botão Mágico',
          body: 'Executa o fluxo completo automaticamente para tentar entregar os arquivos processados com um clique.'
        }
      ],
      video_splitter: [
        {
          target: '[data-tour-target="splitter-duration"]',
          title: 'Duração do trecho',
          body: 'Escolha livre, fixa ou presets rápidos antes de pinçar um trecho na linha do tempo.'
        },
        {
          target: '[data-tour-target="splitter-visual"]',
          title: 'Seleção visual',
          body: 'Use o preview e arraste a faixa ou as pinças para encontrar exatamente o início e o fim do trecho.'
        },
        {
          target: '[data-tour-target="splitter-mode"]',
          title: 'Modo de divisão',
          body: 'Depois de ajustar a seleção visual, escolha se vai dividir automaticamente ou processar os trechos manuais.'
        }
      ]
    };

    const scopeSteps = tours[scope] || tours.home;
    const hasApiAlert = Boolean(document.querySelector('[data-tour-target="api-alert"]'));
    return scopeSteps
      .filter((step) => document.querySelector(step.target))
      .filter((step) => scope === 'home' && hasApiAlert ? step.target !== '[data-tour-target="settings"]' : true)
      .slice(0, 5);
  }

  document.querySelectorAll('[data-onboarding-tour]').forEach((onboardingTour) => {
    const card = onboardingTour.querySelector('[data-tour-card]');
    const kicker = onboardingTour.querySelector('[data-tour-kicker]');
    const title = onboardingTour.querySelector('[data-tour-title]');
    const body = onboardingTour.querySelector('[data-tour-body]');
    const nextButton = onboardingTour.querySelector('[data-tour-next]');
    const steps = getTourSteps(onboardingTour.dataset.tourScope || 'home');

    let currentIndex = 0;
    let currentTarget = null;
    let tourClosed = false;

    function persistTourDismissed() {
      const url = onboardingTour.dataset.dismissUrl;
      if (!url) return;
      fetch(url, {
        method: 'POST',
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'
        },
        body: new URLSearchParams({
          tour_key: onboardingTour.dataset.tourKey || 'home_tour_seen'
        })
      }).catch(() => {});
    }

    function clearTarget() {
      if (currentTarget) {
        currentTarget.classList.remove('is-tour-highlight');
        currentTarget = null;
      }
    }

    function closeTour() {
      if (tourClosed) return;
      tourClosed = true;
      clearTarget();
      onboardingTour.classList.add('is-closing');
      onboardingTour.setAttribute('aria-hidden', 'true');
      persistTourDismissed();
      setTimeout(() => onboardingTour.remove(), 180);
    }

    function clamp(value, min, max) {
      return Math.max(min, Math.min(max, value));
    }

    function placeCard() {
      if (!card || !currentTarget) return;
      const rect = currentTarget.getBoundingClientRect();
      const cardRect = card.getBoundingClientRect();
      const gap = 14;
      const margin = 18;
      const targetCenter = rect.left + rect.width / 2;
      let placement = 'bottom';
      let top = rect.bottom + gap;
      let left = targetCenter - cardRect.width / 2;

      if (top + cardRect.height > window.innerHeight - margin) {
        placement = 'top';
        top = rect.top - cardRect.height - gap;
      }

      if (top < margin) {
        placement = 'bottom';
        top = clamp(rect.bottom + gap, margin, window.innerHeight - cardRect.height - margin);
      }

      left = clamp(left, margin, window.innerWidth - cardRect.width - margin);
      card.dataset.placement = placement;
      card.style.left = `${left}px`;
      card.style.top = `${top}px`;
      card.style.setProperty('--tour-arrow-x', `${clamp(targetCenter - left, 28, cardRect.width - 28)}px`);
    }

    function showStep(index) {
      const step = steps[index];
      if (!step) {
        closeTour();
        return;
      }

      clearTarget();
      currentIndex = index;
      currentTarget = document.querySelector(step.target);
      if (!currentTarget) {
        showStep(index + 1);
        return;
      }

      currentTarget.classList.add('is-tour-highlight');
      if (kicker) kicker.textContent = 'Primeiro uso';
      if (title) title.textContent = step.title;
      if (body) body.textContent = step.body;
      if (nextButton) nextButton.textContent = index === steps.length - 1 ? 'Entendi' : 'Próxima';

      onboardingTour.hidden = false;
      onboardingTour.setAttribute('aria-hidden', 'false');
      placeCard();
      onboardingTour.classList.add('is-visible');
      currentTarget.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
      window.setTimeout(placeCard, 260);
    }

    if (!steps.length || !card) {
      persistTourDismissed();
      onboardingTour.remove();
    } else {
      nextButton?.addEventListener('click', () => showStep(currentIndex + 1));
      onboardingTour.querySelectorAll('[data-tour-dismiss]').forEach((button) => {
        button.addEventListener('click', closeTour);
      });
      window.addEventListener('resize', placeCard);
      window.addEventListener('scroll', placeCard, { passive: true });
      document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') closeTour();
      });
      window.requestAnimationFrame(() => showStep(0));
    }
  });

  document.querySelectorAll('[data-copy-technical-log]').forEach((button) => {
    button.addEventListener('click', async () => {
      const panel = button.closest('.technical-log-panel');
      const log = panel ? panel.querySelector('[data-technical-log]') : null;
      const text = log ? log.textContent : '';
      if (!text) return;
      const originalText = button.textContent;
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = 'Log copiado';
      } catch (_) {
        button.textContent = 'Selecione e copie';
      }
      setTimeout(() => {
        button.textContent = originalText;
      }, 1400);
    });
  });

  const projectSteps = document.querySelector('[data-project-steps]');
  if (projectSteps) {
    const sections = Array.from(projectSteps.querySelectorAll('.project-step'));
    const completedJobMap = {
      generate_transcription: 'step-speakers',
      identify_speakers: 'step-participants',
      suggest_cuts: 'section-4',
      process_cuts: 'section-8'
    };

    function getCompletedJobStep() {
      try {
        const raw = sessionStorage.getItem('evr_completed_progress_job');
        if (!raw) return '';
        sessionStorage.removeItem('evr_completed_progress_job');
        const data = JSON.parse(raw);
        if (!data || !data.jobType || !data.finishedAt) return '';
        if (Date.now() - Number(data.finishedAt) > 120000) return '';
        return completedJobMap[data.jobType] || '';
      } catch (_) {
        return '';
      }
    }

    function setSectionOpen(section, isOpen) {
      section.classList.toggle('is-open', isOpen);
      section.classList.toggle('is-collapsed', !isOpen);
      const button = section.querySelector('.step-toggle');
      if (button) {
        button.setAttribute('aria-expanded', String(isOpen));
        button.setAttribute('aria-label', isOpen ? 'Esconder etapa' : 'Mostrar etapa');
      }
    }

    function closeOtherSections(activeSection) {
      sections.forEach((section) => {
        if (section !== activeSection) setSectionOpen(section, false);
      });
    }

    sections.forEach((section) => {
      const header = section.querySelector('.section-header');
      if (!header) return;

      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'step-toggle';
      button.setAttribute('aria-controls', section.id);
      button.setAttribute('aria-expanded', 'false');
      button.setAttribute('aria-label', 'Mostrar etapa');
      header.appendChild(button);

      header.addEventListener('click', (event) => {
        if (event.target.closest('a, input, select, textarea, label')) return;
        const shouldOpen = !section.classList.contains('is-open');
        if (shouldOpen) closeOtherSections(section);
        setSectionOpen(section, shouldOpen);
      });
    });

    const completedStep = getCompletedJobStep();
    const currentStep = completedStep || projectSteps.dataset.currentStep || '';

    sections.forEach((section) => {
      setSectionOpen(section, section.id === currentStep);
    });
  }
})();
