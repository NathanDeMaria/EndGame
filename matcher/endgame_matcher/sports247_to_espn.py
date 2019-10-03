from typing import Set


LOOKUP = {
    'Brigham Young': 'BYU',
    'N.C. State': 'NC State',
    'Nicholls State': 'Nicholls',
    'FIU': 'Florida International',
    'Hawaii': "Hawai'i",
    'McNeese State': 'McNeese',
    'Connecticut': 'UConn',
    'Southern Miss': 'Southern Mississippi',
    'USF': 'South Florida',
    'Louisiana-Monroe': 'UL Monroe',
    'Wheaton College': 'Wheaton College Illinois',
    'Massachusetts': 'UMass',
    'Tennessee-Martin': 'UT Martin',
    'Prarie View A&M': 'Prarie View',
    'Nebraska Kearney': 'Nebraska-Kearney',
    'Nebraska Omaha': 'Nebraska-Omaha',
    'Minnesota Duluth': 'Minnesota-Duluth',
    # There's also a Augustana College (IL), no idea which. Oh well...
    'Augustana': 'Augustana University (SD)',
    'Virginia Military Institute': 'VMI',
    'U.S. Coast Guard Academy': 'Coast Guard',
    'Trinity (Texas)': 'Trinity University (TX)',
    'Presbyterian': 'Presbyterian College',
    # Apologies if there's another Montana State stealing credit here
    'Montana State-Northern': 'Montana State',
    'IUP': 'Indiana (PA)',
    'Saint Francis (PA)': 'St. Francis (PA)',
    'Prairie View A&M': 'Prairie View',
    # Simon Fraser has a page, but not an ID https://www.espn.com/college-football/team/schedule/_/name/sim/season/2019
    # Dixie State (College) has a page but nothing scheduled? https://www.espn.com/college-football/team/stats/_/id/3101/season/2019
    # Same with Western Oregon https://www.espn.com/college-football/team/stats/_/id/2848/season/2019
    # Texas A&M Commerce https://www.espn.com/college-football/team/stats/_/id/2837/texas-commerce-lions
    # Minnesota State Moorhead https://www.espn.com/college-football/team/schedule/_/id/2817/year/2013
    'University of Charleston': 'Charleston (WV)',
    'Colorado State Pueblo': 'Colorado State-Pueblo',
    'Minnesota State Mankato': 'Minnesota State',
}


class Translator:
    def __init__(self, espn_locations: Set[str]):
        self._espn_locations = espn_locations

    def translate(self, sports247_name: str) -> str:
        """
        Lookup from the 247sports name to the ESPN name (location)
        """
        if sports247_name in self._espn_locations:
            return sports247_name
        try:
            return LOOKUP[sports247_name]
        except KeyError:
            # RULE: you need at least 2 that fit a pattern to add a non-lookup here.

            # Apparently ESPN drops state sometimes,
            # like Nicholls or McNeese
            if sports247_name.endswith(' State'):
                without_state = sports247_name[:-6]
                return self.translate(without_state)

            # Southern and Ashland University
            if sports247_name.endswith(' University'):
                without_uni = sports247_name[:-11]
                return self.translate(without_uni)
            
            # Stillman and Paine College
            if sports247_name.endswith(' College'):
                without_college = sports247_name[:-8]
                return self.translate(without_college)

