/* One cancel mechanism for work that a later change makes stale (a reload,
   a search typed on, a map closed, a new stop pair): bump() on the change,
   and a task keeps the token() it started with and drops its answer once
   stale(token). Nothing is aborted; a stale answer is only not used. */
export function generation() {
  let n = 0;
  return {
    bump() {
      n += 1;
      return n;
    },
    token() {
      return n;
    },
    stale(token) {
      return token !== n;
    },
  };
}
