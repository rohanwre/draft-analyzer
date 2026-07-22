import type { Recommendation } from "../api/types";
import TrendBar from "./TrendBar";
import PositionalNeedsList from "./PositionalNeedsList";
import ScarcityAlerts from "./ScarcityAlerts";
import RankedPlayersList from "./RankedPlayersList";

interface Props {
  recommendation: Recommendation;
}

export default function RecommendationPanel({ recommendation: rec }: Props) {
  return (
    <div className="recommendation-panel">
      <div className="panel-section">
        <h3>
          {rec.trend_source === "similar_drafts"
            ? `Based on ${rec.sample_size} similar historical drafts`
            : `General round trends (all ${rec.league_size}-team, ${rec.league_type} leagues)`}
        </h3>
        {rec.trends.map((t) => (
          <TrendBar key={t.position} position={t.position} pct={t.top_two_pct} />
        ))}
      </div>

      <PositionalNeedsList needs={rec.positional_needs} />

      <RankedPlayersList players={rec.ranked_players} />

      <div className="panel-section">
        <h3>Top available by position</h3>
        <p className="section-hint">Ranked by historical trend — real ADP and trend % shown side by side, not blended into one score.</p>
        {rec.top_available_by_position.map((group) => (
          <div key={group.position} className="position-group">
            <div className="position-group-header">
              <strong>{group.position}</strong> — {group.top_two_pct.toFixed(1)}% of top 2 seeds
              {group.need === "urgent" && <span className="tag tag-urgent">URGENT NEED</span>}
              {group.need === "need" && <span className="tag tag-need">NEED</span>}
            </div>
            <ul className="player-list">
              {group.players.map((p) => (
                <li key={p.name}>
                  {p.name} <span className="adp">ADP {p.rank}</span>
                </li>
              ))}
              {group.players.length === 0 && <li className="empty">No players left at this position</li>}
            </ul>
          </div>
        ))}
      </div>

      <div className="panel-section">
        <h3>Value picks (available past their ADP)</h3>
        {rec.value_picks.length === 0 ? (
          <p className="section-hint">None right now.</p>
        ) : (
          <ul className="player-list">
            {rec.value_picks.map((vp) => (
              <li key={vp.name}>
                {vp.name} ({vp.position}) <span className="adp">ADP {vp.rank}</span>{" "}
                <span className="value">value: +{vp.value.toFixed(0)} picks</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <ScarcityAlerts alerts={rec.scarcity_alerts} />
    </div>
  );
}
