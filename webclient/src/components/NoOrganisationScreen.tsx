import { useAuth } from '../contexts/AuthContext'
import { useOrganization } from '../contexts/OrganizationContext'
import { getAppLogo, getAppTitle } from '../branding'

/**
 * Shown to a signed-in user who is not a member of any organisation.
 *
 * Before this screen existed the app sat on "Loading data" for ever: the data
 * loader only runs once an organisation is selected, and it is the only thing
 * that clears the loading flag, so a user with no organisation never got past
 * the spinner and had no way of knowing why. That is exactly where an invitee
 * ended up when their invitation had not been honoured yet.
 *
 * The screen says what is true (signed in, no organisation), what to do about
 * it (get invited, or open the invitation link), and offers the two actions
 * that can change the situation: check again, or sign out and come back with
 * the right account.
 */
export default function NoOrganisationScreen() {
  const { user, logout } = useAuth()
  const { refreshOrganizations } = useOrganization()
  const appLogo = getAppLogo()
  const appTitle = getAppTitle()

  return (
    <div className="auth-page">
      <div className="auth-page-container">
        <div className="auth-card" role="status" aria-live="polite">
          {appLogo && <img src={appLogo} alt="Logo" className="auth-logo" />}
          <h1 className="auth-title">{appTitle}</h1>
          <p className="auth-subtitle">No organisation yet</p>
          <p className="auth-help-text">
            You are signed in{user?.email ? <> as <strong>{user.email}</strong></> : null}, but
            this account is not a member of any organisation.
          </p>
          <p className="auth-help-text">
            Ask an administrator to invite you from <strong>Settings › User Management</strong>.
            If you already have an invitation email, open the link in it.
          </p>
          <button type="button" className="auth-google-btn" onClick={() => refreshOrganizations()}>
            Check again
          </button>
          <button type="button" className="auth-link-btn" onClick={logout}>
            Sign out
          </button>
        </div>
      </div>
    </div>
  )
}
