import { PanelSectionRow, DropdownItem } from "@decky/ui";

interface GameSelectorProps {
  games: any[];
  selectedGame: any;
  setSelectedGame: (game: any) => void;
}

export const GameSelector = ({ games, selectedGame, setSelectedGame }: GameSelectorProps) => {
  return (
    <PanelSectionRow>
      <DropdownItem
        rgOptions={games.map(g => ({ data: g, label: g.name }))}
        selectedOption={selectedGame}
        onChange={(opt) => setSelectedGame(opt.data)}
        strDefaultLabel="Select Game..."
      />
    </PanelSectionRow>
  );
};
