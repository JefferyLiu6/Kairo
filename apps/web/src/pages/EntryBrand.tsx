import { Link } from "react-router-dom";
import "../styles/entry.css";

export function EntryBrand() {
  return <Link to="/" className="entry-brand" aria-label="Kairo home">
    <img src="/kairo-logo.svg" width="34" height="34" alt="" />Kairo
  </Link>;
}
