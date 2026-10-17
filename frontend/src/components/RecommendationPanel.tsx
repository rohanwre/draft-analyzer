import type { Recommendation } from "../api/types";
import TrendBar from "./TrendBar";
import PositionalNeedsList from "./PositionalNeedsList";
import ScarcityAlerts from "./ScarcityAlerts";
import RankedPlayersList from "./RankedPlayersList";

interface Props {
  recommendation: Recommendation;
  onSelectPlayer: (name: string, position: string) => void;
  disabled: boolean;
}

export default function RecommendationPanel({ recommendation: rec, onSelectPlayer, disabled }: Props) {
  return (
    <div className="recommendation-panel">
      <div className="panel-section">
        <h3>What winning teams took here</h3>
        <p className="section-hint">
          {rec.trend_source === "similar_drafts"
            ? `From ${(rec.sample_size ?? 0).toLocaleString()} historical drafts that looked like yours at this point`
            : `General round trends across all ${rec.league_size}-team ${rec.league_type === "qb_premium" ? "superflex" : "1QB"} leagues`}
          {rec.league_format === "dynasty" ? ", dynasty startups" : ""}
          {rec.scoring_type === "half_ppr" ? ", half-PPR" : rec.scoring_type === "standard" ? ", standard scoring" : ""}.
          Share of picks by teams that finished in their league's top 20%.
        </p>
        {rec.trends.map((t) => (
          <TrendBar key={t.position} position={t.position} pct={t.top_two_pct} />
        ))}
      </div>

      <PositionalNeedsList needs={rec.positional_needs} />

      <RankedPlayersList players={rec.ranked_players} onSelectPlayer={onSelectPlayer} disabled={disabled} />

      <div className="panel-section">
        <h3>Top available by position</h3>
        <p className="section-hint">
          Positions ordered by historical trend %. ADP and trend are shown side by side, not blended.
        </p>
        <div className="position-groups-row">
          {rec.top_available_by_position.map((group) => (
            <div key={group.position} className="position-group">
              <div className="position-group-header">
                <span className={`pos pos-${group.position}`}>{group.position}</span>
                <span className="num">{group.top_two_pct.toFixed(1)}%</span>
                {group.need === "urgent" && <span className="tag tag-urgent">Urgent</span>}
                {group.need === "need" && <span className="tag tag-need">Need</span>}
              </div>
              <ul className="player-list">
                {group.players.map((p) => (
                  <li key={p.name} className="player-list-item">
                    <span className="player-list-name">
                      {p.name} <span className="adp">ADP {p.rank}</span>
                    </span>
                    <button
                      type="button"
                      className="select-btn"
                      disabled={disabled}
                      onClick={() => onSelectPlayer(p.name, group.position)}
                    >
                      Select
                    </button>
                  </li>
                ))}
                {group.players.length === 0 && <li className="empty">None left</li>}
              </ul>
            </div>
          ))}
        </div>
      </div>

      <div className="panel-section">
        <h3>Value picks (available past their ADP)</h3>
        {rec.value_picks.length === 0 ? (
          <p className="section-hint">None right now.</p>
        ) : (
          <ul className="player-list">
            {rec.value_picks.map((vp) => (
              <li key={vp.name} className="player-list-item">
                <span className="player-list-name">
                  <span className={`pos pos-${vp.position}`}>{vp.position}</span> {vp.name} <span className="adp">ADP {vp.rank}</span>{" "}
                  <span className="value num">+{vp.value.toFixed(0)} picks</span>
                </span>
                <button
                  type="button"
                  className="select-btn"
                  disabled={disabled}
                  onClick={() => onSelectPlayer(vp.name, vp.position)}
                >
                  Select
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <ScarcityAlerts alerts={rec.scarcity_alerts} />
    </div>
  );
}
