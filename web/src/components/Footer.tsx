import { formatDate } from "../text";

export const SOURCE_CODE_URL = "https://github.com/azterizm/chapter-and-verse";
const OGL_URL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/";

export function Footer({ snapshot }: { snapshot: string | null }) {
  return (
    <footer className="site-footer">
      <p>
        <strong>Information about legislation, not legal advice.</strong> Do not enter client details or personal data.
      </p>
      <p>
        Contains public sector information licensed under the{" "}
        <a href={OGL_URL} rel="noopener noreferrer" target="_blank">
          Open Government Licence v3.0
        </a>
        . Source: legislation.gov.uk, © Crown and database right.
      </p>
      <p>
        Corpus snapshot {snapshot ? formatDate(snapshot) : "not known"}.{" "}
        <a href={SOURCE_CODE_URL} rel="noopener noreferrer" target="_blank">
          Source code
        </a>{" "}
        under AGPL-3.0.
      </p>
    </footer>
  );
}
