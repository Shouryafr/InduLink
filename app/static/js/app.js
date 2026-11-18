/* InduLink interface behaviour.
   No inline scripts anywhere (strict CSP): everything is wired from here.
   Progressive enhancement only — every feature works without JavaScript. */
(function () {
  'use strict';

  var root = document.documentElement;

  /* --- Appearance ------------------------------------------------------- */
  function effectiveTheme() {
    var explicit = root.getAttribute('data-theme');
    if (explicit === 'dark' || explicit === 'light') return explicit;
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
      ? 'dark'
      : 'light';
  }

  document.querySelectorAll('[data-theme-toggle]').forEach(function (button) {
    button.addEventListener('click', function () {
      var next = effectiveTheme() === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try {
        window.localStorage.setItem('indulink-theme', next);
      } catch (error) {
        /* Storage unavailable: the choice lasts for this page only. */
      }
      button.setAttribute(
        'aria-label',
        next === 'dark' ? 'Switch to light appearance' : 'Switch to dark appearance'
      );
    });
  });

  /* --- Evidence badge popovers ----------------------------------------- */
  function closeAllPopovers(except) {
    document.querySelectorAll('.badge-wrap__pop:not([hidden])').forEach(function (pop) {
      if (pop === except) return;
      pop.hidden = true;
      var trigger = pop.parentElement.querySelector('[data-badge]');
      if (trigger) trigger.setAttribute('aria-expanded', 'false');
    });
  }

  document.querySelectorAll('[data-badge]').forEach(function (trigger) {
    var pop = trigger.parentElement.querySelector('.badge-wrap__pop');
    if (!pop) return;
    trigger.addEventListener('click', function (event) {
      event.stopPropagation();
      var willOpen = pop.hidden;
      closeAllPopovers(pop);
      pop.hidden = !willOpen;
      trigger.setAttribute('aria-expanded', String(willOpen));
    });
  });

  document.addEventListener('click', function () {
    closeAllPopovers(null);
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closeAllPopovers(null);
  });

  /* --- Character counters ----------------------------------------------- */
  document.querySelectorAll('[data-counter]').forEach(function (input) {
    var output = document.getElementById(input.getAttribute('data-counter'));
    if (!output) return;
    var max = Number(input.getAttribute('maxlength')) || 0;
    function update() {
      var used = input.value.length;
      output.textContent = max ? used + ' / ' + max : String(used);
      output.classList.toggle('counter--over', max > 0 && used > max);
    }
    input.addEventListener('input', update);
    update();
  });

  /* --- Requirement rows in the challenge form --------------------------- */
  var rowHost = document.getElementById('requirement-rows');
  var rowTemplate = document.getElementById('requirement-row-template');
  var addRow = document.querySelector('[data-add-requirement]');

  if (rowHost && rowTemplate && addRow) {
    addRow.addEventListener('click', function () {
      var rows = rowHost.querySelectorAll('[data-requirement-row]');
      if (rows.length >= 12) {
        addRow.disabled = true;
        return;
      }
      var fragment = rowTemplate.content.cloneNode(true);
      rowHost.appendChild(fragment);
    });

    rowHost.addEventListener('click', function (event) {
      var remove = event.target.closest('[data-remove-requirement]');
      if (!remove) return;
      var rows = rowHost.querySelectorAll('[data-requirement-row]');
      if (rows.length <= 1) return;
      remove.closest('[data-requirement-row]').remove();
    });
  }

  /* --- Confirmation for consequential actions --------------------------- */
  var pendingForm = null;

  function confirmDialog(message) {
    if (typeof document.createElement('dialog').showModal !== 'function') {
      return window.confirm(message);
    }
    var dialog = document.createElement('dialog');
    dialog.className = 'modal';
    var heading = document.createElement('h2');
    heading.textContent = 'Please confirm';
    var body = document.createElement('p');
    body.textContent = message;
    var actions = document.createElement('div');
    actions.className = 'modal__actions';
    var cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'btn btn--secondary';
    cancel.textContent = 'Cancel';
    var go = document.createElement('button');
    go.type = 'button';
    go.className = 'btn btn--primary';
    go.textContent = 'Continue';
    actions.appendChild(cancel);
    actions.appendChild(go);
    dialog.appendChild(heading);
    dialog.appendChild(body);
    dialog.appendChild(actions);
    document.body.appendChild(dialog);
    dialog.showModal();

    function finish(result) {
      dialog.close();
      dialog.remove();
      if (result && pendingForm) pendingForm.submit();
      pendingForm = null;
    }
    cancel.addEventListener('click', function () {
      finish(false);
    });
    go.addEventListener('click', function () {
      finish(true);
    });
    dialog.addEventListener('cancel', function () {
      finish(false);
    });
    return false;
  }

  document.querySelectorAll('form[data-confirm]').forEach(function (form) {
    form.addEventListener('submit', function (event) {
      if (form.dataset.confirmed === 'true') return;
      event.preventDefault();
      pendingForm = form;
      var result = confirmDialog(form.getAttribute('data-confirm'));
      if (result === true) {
        form.dataset.confirmed = 'true';
        form.submit();
      }
    });
  });

  /* --- Toasts ----------------------------------------------------------- */
  var region = document.getElementById('toast-region');

  function toast(message, tone) {
    if (!region) return;
    var node = document.createElement('div');
    node.className = 'toast' + (tone ? ' toast--' + tone : '');
    node.textContent = message;
    region.appendChild(node);
    window.setTimeout(function () {
      node.remove();
    }, 6000);
  }

  document.querySelectorAll('[data-toast]').forEach(function (node) {
    toast(node.getAttribute('data-toast'), node.getAttribute('data-toast-tone'));
  });
})();
