import { Link, NavLink, Route, Routes } from "react-router-dom";
import { LiveBridge } from "./components/LiveBridge";
import { SearchBox } from "./components/SearchBox";
import { Message } from "./components/PageState";
import { GamePage } from "./pages/Game";
import { HistoryPage } from "./pages/History";
import { LivePage } from "./pages/Live";
import { PlayerPage } from "./pages/Player";
import { TeamPage } from "./pages/Team";

export function App() {
  return (
    <>
      <LiveBridge />
      <header className="topbar">
        <div className="topbar-inner">
          <Link to="/" className="brand">
            <span className="brand-dot" aria-hidden="true" />
            Player Scout
          </Link>
          <SearchBox />
          <nav className="tabs" aria-label="Main">
            <NavLink to="/" end>
              Live
            </NavLink>
            <NavLink to="/history">History</NavLink>
          </nav>
        </div>
      </header>
      <main className="page">
        <Routes>
          <Route path="/" element={<LivePage />} />
          <Route path="/games/:gameId" element={<GamePage />} />
          <Route path="/players/:playerId" element={<PlayerPage />} />
          <Route path="/teams/:teamId" element={<TeamPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route
            path="*"
            element={
              <Message title="Page not found">
                <Link to="/">Back to today's games</Link>
              </Message>
            }
          />
        </Routes>
      </main>
    </>
  );
}
