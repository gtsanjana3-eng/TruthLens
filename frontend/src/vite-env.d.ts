/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** e.g. https://<user>-truthlens.hf.space – leave unset for local development (Vite proxy). */
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
