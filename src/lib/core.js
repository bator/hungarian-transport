/* BkkLib is the library's namespace: the lib/*.js modules add their members
   through register(), so they can keep calling each other as BkkLib.x()
   across files. */
export const BkkLib = {};

/* Adds members to BkkLib. A name two files define is a load error, not a
   silent overwrite that depends on the import order. */
export function register(members) {
  Object.keys(members).forEach((key) => {
    if (Object.prototype.hasOwnProperty.call(BkkLib, key)) {
      throw new Error(`BkkLib.${key} is registered twice`);
    }
  });
  Object.assign(BkkLib, members);
  return BkkLib;
}
