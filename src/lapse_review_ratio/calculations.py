from aqt import AnkiQt


def average_lapse_review_ratio(mw: AnkiQt) -> float:
    cards_data = mw.col.db.all("select id, lapses from cards;")
    ratio_sum_holder = 0
    for card_data in cards_data:
        card_id = card_data[0]
        card_lapse_count = card_data[1]
        review_count = mw.col.db.first("select count(*) from revlog where cid = ?", card_id)[0]
        if review_count == 0:
            continue
        lapse_review_ratio = card_lapse_count / review_count
        ratio_sum_holder += lapse_review_ratio
    card_count = mw.col.db.first("select count(*) from cards")[0]
    return ratio_sum_holder / card_count


def lr_ratio_for_card(mw: AnkiQt, card_id: int):
    card_data = mw.col.db.first("select id, lapses from cards where id = ?", card_id)
    card_lapse_count = card_data[1]
    review_count = mw.col.db.first("select count(*) from revlog where cid = ?", card_id)[0]
    if review_count == 0:
        return 0
    return card_lapse_count / review_count


def lr_ratio_for_note(mw: AnkiQt, note_id: int):
    cards_data = mw.col.db.all("select id, lapses from cards where nid = ?", note_id)
    number_of_cards: int = len(cards_data)
    sum_of_card_ratios = 0
    for card_data in cards_data:
        card_id = card_data[0]
        card_lapse_count = card_data[1]
        review_count = mw.col.db.first("select count(*) from revlog where cid = ?", card_id)[0]
        if review_count == 0:
            continue
        lapse_review_ratio = card_lapse_count / review_count
        sum_of_card_ratios += lapse_review_ratio
    return sum_of_card_ratios / number_of_cards
