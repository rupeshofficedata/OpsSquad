// Express 4 does not catch rejected promises from async route handlers —
// an unhandled rejection there crashes the whole process on modern Node.
// Wrap every async handler with this so errors reach the error middleware
// in server.js instead.
export function asyncHandler(fn) {
  return (req, res, next) => {
    Promise.resolve(fn(req, res, next)).catch(next);
  };
}
