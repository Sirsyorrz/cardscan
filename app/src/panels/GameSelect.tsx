import { GAMES } from "../api";

type Props = {
  value: string;
  onChange: (game: string) => void;
  allLabel?: string;
  count?: (game: string) => number;
};

export default function GameSelect({ value, onChange, allLabel = "All games", count }: Props) {
  const label = (text: string, key: string) => (count ? `${text} (${count(key)})` : text);
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{label(allLabel, "")}</option>
      {GAMES.map((g) => (
        <option key={g.key} value={g.key}>
          {label(g.label, g.key)}
        </option>
      ))}
    </select>
  );
}
