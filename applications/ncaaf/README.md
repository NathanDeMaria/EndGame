# NCAAFB

Scripts for collecting NCAAFB data (games, team names, recruiting ratings), and some EDA on it.

This is its own RStudio project, a client of the `EndGame` package in the root of this repo.

## Data

- [`ncaa_fb.R`](./ncaa_fb.R): reads games from FBS/FCS/DII/DIII
  - Outputs `ncaafb.csv`
  - I have since 2001 for FBS, but I think ESPN took those down before I implemented other divisions
  - Each row is a game
  - Columns:
    - `home`: the ESPN team location + display name
    - `home_score`
    - `home_conference`: the ESPN conference ID the home team was a member of during the season 
    - `away`
    - `away_score`
    - `away_conference`
    - `date`: the date/time (UTC)
    - `neutral_site`: TRUE if the game was played at a neutral site
    - `completed`: TRUE if the game was completed. Should all be true for this `.csv`, but potentially false for one aimed at future games
    - `week`: the week number of this game. Post-season (bowls, playoffs) weeks start at 16
    - `season`: the year at the start of the season this game is a part of
  - Also outputs `ncaaf_conferences.csv`
  - Each row is a team for a season
  - Columns:
    - `season`
    - `name`: the ESPN team location + display name
    - `group`: the ESPN conference ID the home team was a member of during the season 
- [`cfb_teams.R`](./cfb_teams.R): Loop through networks of teams to try to find everybody's name/ID according to ESPN
  - Outputs `ncaaf_team_info.csv`
  - Loop goes back to 2003 (furthest back ESPN's UI shows team schedules right now)
  - Each row is an NCAAFB team
  - Columns:
    - `espn_location`: the location-name of the team, like Nebraska or Oklahoma State
    - `espn_display_name`: the mascot, like Huskers or Cowboys
    - `espn_team_id`: the ID of the team as it appears in ESPN.com links
- [`247_cfb.R`](./247_cfb.R): Recruiting ratings
  - Outputs `ncaaf_recruiting.csv`
  - Since 2000, but more detail is added on later years. See [`247.Rmd`](./247.Rmd) for more
  - Each row is a team for a season
  - Columns:
    - `name`: team name, usual close to ESPN's location
    - `rating`: total rating of recruits coming in this season
    - `year`: recruiting year (season when players would be true freshmen)


## Analysis

- [`RealTeams.Rmd`](RealTeams.Rmd): try to figure out which teams to include in the `py-glicko` ratings.
- [`Recruiting.Rmd`](RealTeams.Rmd): figure out how to represent recruiting ratings
