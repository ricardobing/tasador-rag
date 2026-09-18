import { redirect } from "next/navigation";

/** doc 07 §1: `/` redirige al listado, que es donde vive el usuario. */
export default function Home() {
  redirect("/informes");
}
