import { useEffect } from "react";
import { Link } from "react-router";

import { useConfig } from "../../app/queries";
import { t } from "../../lib/strings";

/** Privacy notice (Digital Personal Data Protection Act, 2023). */
export function PrivacyPage() {
  const config = useConfig().data;
  const authority = config?.authority_name ?? "the council";
  useEffect(() => {
    document.title = `${t.common.privacy} · ${t.appName}`;
  }, []);

  return (
    <div className="page prose">
      <h1>{t.common.privacy}</h1>
      <p>
        NursingBot answers questions about nurse registration, renewal, admissions and
        counselling from the official documents of the {authority}, and general nursing
        questions from general knowledge. This notice explains what it stores and why.
      </p>

      <h2>Without an account</h2>
      <ul>
        <li>Your chats are kept only in this browser. Clearing the site's data deletes them.</li>
        <li>
          The server issues an anonymous session that counts usage. It does not store your
          questions unless the administrator has turned on guest history, and then only with
          phone numbers, e-mail addresses and Aadhaar numbers removed.
        </li>
      </ul>

      <h2>With an account</h2>
      <ul>
        <li>
          We store your e-mail address, an optional name, a securely hashed password (never
          the password itself), and the chats you have while signed in, so you can see them on
          any device.
        </li>
        <li>
          Your e-mail address is only your sign-in name. It is not verified and we do not send
          e-mail.
        </li>
        <li>
          If you rate an answer, the rated question and answer may be reviewed by the
          administrator, without your name or e-mail, to improve the service.
        </li>
        <li>Administrators cannot read your chats.</li>
      </ul>

      <h2>Your rights</h2>
      <ul>
        <li>
          Download everything stored about you, or delete your account and all your chats, at
          any time in <Link to="/settings">Settings</Link>.
        </li>
        <li>Delete single chats from the sidebar.</li>
        <li>
          For any other request, contact the {authority}
          {config?.contact_url && (
            <>
              {" "}
              (
              <a href={config.contact_url} target="_blank" rel="noopener noreferrer">
                contact page
              </a>
              )
            </>
          )}
          .
        </li>
      </ul>

      <h2>How your data is protected</h2>
      <ul>
        <li>
          Phone numbers, e-mail addresses and Aadhaar numbers are removed from questions before
          they are sent to the AI service, and from diagnostic traces.
        </li>
        <li>Data is stored in a database operated for the service and backed up regularly.</li>
        <li>
          Until the service has a domain name with HTTPS, connections from outside the local
          network are not encrypted. Do not reuse a password you use elsewhere.
        </li>
      </ul>

      <h2>Answers</h2>
      <p>
        Answers marked "From official records" cite the documents they come from. Answers marked
        "General nursing knowledge" are not official information. NursingBot does not give
        personal medical advice: in an emergency call 108.
      </p>
    </div>
  );
}
