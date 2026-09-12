/**
 * The session: a JWT from POST /api/auth/login and the user it names.
 * Kept in localStorage so a reload keeps you signed in until the token
 * expires (the director decides: SC__UI__JWT_TTL_MINUTES). Modules that
 * cannot use React (the API client) read it through this tiny store.
 */
export type Role = "viewer" | "approver" | "admin";

export interface SessionUser {
  email: string;
  name: string;
  role: Role;
}

export interface Session {
  token: string;
  user: SessionUser;
}

const STORAGE_KEY = "control-tower.session";
type Listener = (session: Session | null) => void;

function read(): Session | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

class AuthStore {
  private session: Session | null = read();
  private listeners = new Set<Listener>();

  get(): Session | null {
    return this.session;
  }

  getToken(): string | null {
    return this.session?.token ?? null;
  }

  set(session: Session): void {
    this.session = session;
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    } catch {
      /* ignore */
    }
    this.emit();
  }

  clear(): void {
    this.session = null;
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* ignore */
    }
    this.emit();
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private emit(): void {
    for (const listener of this.listeners) listener(this.session);
  }
}

export const authStore = new AuthStore();

const RANK: Record<Role, number> = { viewer: 0, approver: 1, admin: 2 };

export function atLeast(role: Role | undefined, wanted: Role): boolean {
  return role !== undefined && RANK[role] >= RANK[wanted];
}
