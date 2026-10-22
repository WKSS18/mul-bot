import {beforeEach} from 'vitest';

globalThis.ResizeObserver = class {observe(){} unobserve(){} disconnect(){}};
globalThis.matchMedia = () => ({matches:false,addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}});
globalThis.HTMLElement.prototype.scrollIntoView = function() {};

beforeEach(() => {
  localStorage.clear();
  history.replaceState({}, '', '/');
});
