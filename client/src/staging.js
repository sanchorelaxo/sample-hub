// Staged transfer store (TODO_1.txt item 5). A legal drop stages a
// source -> sink change; nothing reaches the server until Apply. Cancel clears
// every staged change; Undo removes only the most recent one.
const changes = [];

export function stage(sourceDevice, sourceRef, sourceType, target) {
  changes.push({ sourceDevice, sourceRef, sourceType, target });
  return changes.length;
}

export function pending() {
  return changes.slice();
}

export function pendingFor(target) {
  return changes.filter((change) => change.target === target);
}

export function undoLast() {
  return changes.pop();
}

export function cancelAll() {
  changes.length = 0;
}

export function applyChanges() {
  return changes.splice(0, changes.length);
}
