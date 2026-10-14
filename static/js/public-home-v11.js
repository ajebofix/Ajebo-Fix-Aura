/* Aura public homepage: accessible demo tabs and request-type guidance only.
   This script does not fetch vehicle data, submit forms or call an AI provider. */
(() => {
  const tablist = document.querySelector('.product-tabs');
  if (tablist) {
    const tabs = [...tablist.querySelectorAll('[role="tab"]')];
    const activate = (tab, focus = false) => {
      tabs.forEach((candidate) => {
        const selected = candidate === tab;
        candidate.setAttribute('aria-selected', String(selected));
        candidate.tabIndex = selected ? 0 : -1;
        const panel = document.getElementById(candidate.getAttribute('aria-controls'));
        if (panel) panel.hidden = !selected;
      });
      if (focus) tab.focus();
    };
    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => activate(tab));
      tab.addEventListener('keydown', (event) => {
        let next;
        if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
        else if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
        else if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = tabs.length - 1;
        if (next !== undefined) {
          event.preventDefault();
          activate(tabs[next], true);
        }
      });
    });
  }
  const choice = document.getElementById('request_type');
  const help = document.getElementById('request-type-help');
  if (choice && help) {
    const descriptions = {
      private_access: 'Private access is reviewed individually. This does not create an Aura account.',
      assessment: 'Professional assessment is a paid service starting from ₦50,000, depending on the scope. This form is only an enquiry.',
      fleet: 'Tell us about the number of vehicles or your proposed partnership. Our team will discuss the appropriate next steps.'
    };
    const names = {
      private_access: 'Private Aura access',
      assessment: 'Professional vehicle assessment',
      fleet: 'Fleet / partnership enquiry'
    };
    const summaries = {
      private_access: 'Explore continuity-led vehicle care. Your request does not automatically create an account.',
      assessment: 'A paid assessment starts from ₦50,000, depending on the vehicle and scope. This is an enquiry, not a confirmed booking.',
      fleet: 'Tell us about your fleet or proposed partnership. Ajebo Fix will review the opportunity and follow up.'
    };
    const paths = [...document.querySelectorAll('.enquiry-pathways a[data-request-type]')];
    const intentTitle = document.getElementById('request-intent-title');
    const intentDescription = document.getElementById('request-intent-description');
    const formPanel = document.getElementById('request-form-panel');
    const update = () => {
      const intent = Object.hasOwn(names, choice.value) ? choice.value : 'private_access';
      help.textContent = descriptions[intent];
      if (intentTitle) intentTitle.textContent = names[intent];
      if (intentDescription) intentDescription.textContent = summaries[intent];
      paths.forEach((link) => {
        const selected = link.dataset.requestType === intent;
        link.classList.toggle('is-selected', selected);
        if (selected) link.setAttribute('aria-current', 'location');
        else link.removeAttribute('aria-current');
      });
    };
    choice.addEventListener('change', update);
    update();
    if (formPanel) {
      paths.forEach((link) => {
        link.addEventListener('click', (event) => {
          if (event.defaultPrevented || event.button !== 0 ||
              event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          const intent = link.dataset.requestType;
          if (!Object.hasOwn(names, intent)) return;
          event.preventDefault();
          choice.value = intent;
          choice.dispatchEvent(new Event('change', { bubbles: true }));
          // Keep a working URL for sharing and preserve native-link fallback
          // for browsers with JavaScript disabled.
          if (window.history && window.history.replaceState) {
            window.history.replaceState(null, '', link.href);
          }
          formPanel.scrollIntoView({ behavior: 'auto', block: 'start' });
          formPanel.focus({ preventScroll: true });
        });
      });
    }
  }
})();
