/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** `1` builds the buddy rig spike's harness route (`src/buddyRig/`); a release build leaves it unset. */
  readonly VITE_BUDDY_RIG?: string
}
