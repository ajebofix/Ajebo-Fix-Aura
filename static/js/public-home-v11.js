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
    const update = () => { help.textContent = descriptions[choice.value] || descriptions.private_access; };
    choice.addEventListener('change', update);
    update();
  }
})();
