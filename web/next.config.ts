import type { NextConfig } from "next";

const config: NextConfig = {
  // `standalone` deja un server.js con solo lo necesario. Es lo que permite que
  // el contenedor corra con `read_only: true` y 512M (docker-compose.yml).
  output: "standalone",
  reactStrictMode: true,
  // El globo de Next abajo a la izquierda en desarrollo tapa el pie de la barra.
  devIndicators: false,
  // La API nunca se expone al navegador directamente: el front habla con ella
  // por el nombre de servicio interno de Docker. Así el token de sesión (cuando
  // exista auth, Etapa 4) no sale del servidor.
  env: {
    API_INTERNAL_URL: process.env.API_INTERNAL_URL ?? "http://api:8000",
  },
};

export default config;
