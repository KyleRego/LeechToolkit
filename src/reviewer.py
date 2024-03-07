"""
MIT License: Copyright (c) 2022 JustKoi (iamjustkoi) <https://github.com/iamjustkoi>
Full license text available in "LICENSE" file packaged with the program.
"""

from __future__ import annotations

import os
import traceback

import anki.cards
import aqt.reviewer
from anki import hooks
from aqt.utils import showInfo, tooltip
from aqt.webview import WebContent, AnkiWebView
from aqt import gui_hooks, mw
from aqt.reviewer import Reviewer
from aqt.qt import QShortcut, QKeySequence

from .legacy import _try_check_filtered, _try_get_config_dict_for_did, _try_get_current_did
from .actions import handle_actions, handle_reverse
from .config import LeechToolkitConfigManager, merge_fields
from .consts import (
    ANKI_LEGACY_VER,
    ANKI_UNDO_UPDATE_VER,
    CURRENT_ANKI_VER,
    Config,
    ErrorMsg,
    MARKER_ID, MARKER_POS_STYLES,
    LRR_HTML_ID,
    LEECH_TAG,
    MARKER_HTML_TEMP, ROOT_DIR, String,
)
from .lapse_review_ratio import calculations

try:
    from anki.decks import DeckId
    from anki.errors import InvalidInput
except ImportError:
    print(f'{traceback.format_exc()}\n{ErrorMsg.MODULE_NOT_FOUND_LEGACY}')
    DeckId = int

PREV_TYPE_ATTR = 'prevtype'
WRAPPER_ATTR = 'toolkit_manager'


def build_hooks():
    from aqt.gui_hooks import (
        webview_will_set_content,
    )

    webview_will_set_content.append(try_append_wrapper)


def try_append_wrapper(content: aqt.webview.WebContent, context: object):
    """
    Attempts to attach to the current reviewer, as long as it's not a filtered deck, else removes the wrapper.

    :param content: web-content for html and page edits
    :param context: used for checking whether the webview is being set to the reviewer
    """
    if isinstance(context, Reviewer):
        reviewer: aqt.reviewer.Reviewer = context
        if _try_check_filtered(_try_get_current_did()) and hasattr(mw.reviewer, WRAPPER_ATTR):
            mw.reviewer.__delattr__(WRAPPER_ATTR)
        else:
            # Attached for calls and any future garbage collection, potentially, idk
            reviewer.toolkit_wrapper = ReviewWrapper(reviewer, content, _try_get_current_did())


def set_marker_color(color: str):
    """
    Psuedo-tints the leech marker to the input color.

    :param color: color (style) string to update the marker color to
    """
    mw.web.eval(f'document.getElementById("{MARKER_ID}").style.textShadow = "0 0 0 {color}";')


def show_marker(show=False):
    """
    Changes the display state of the handle_input_action marker.

    :param show: new visibility
    """
    if show:
        mw.web.eval(f'document.getElementById("{MARKER_ID}").style.display = "unset"')
    else:
        mw.web.eval(f'document.getElementById("{MARKER_ID}").style.display = "none"')


class ReviewWrapper:
    toolkit_config: dict
    max_fails: int
    did: DeckId
    content: aqt.webview.WebContent
    card: anki.cards.Card
    on_front: bool
    leeched_cids: set[int] = set()

    # queued_undo_entry: int = -1

    def __init__(self, reviewer: Reviewer, content: aqt.webview.WebContent, did: DeckId):
        """
        Wrapper used for handling events in the Anki reviewer, if not a filtered review-type.

        :param reviewer: Anki Reviewer object
        :param content: web-content used for editing the page style/html
        :param did: deck id of the current reviewer
        """
        if not _try_check_filtered(did):
            self.content = content
            self.reviewer = reviewer
            self.load_options(did)

            leech_seq = QKeySequence(self.toolkit_config[Config.SHORTCUT_OPTIONS][Config.LEECH_SHORTCUT])
            leech_shortcut = QShortcut(leech_seq, mw, lambda *args: self.handle_input_action(Config.LEECH_ACTIONS))

            self.leech_action = aqt.qt.QAction(String.REVIEWER_ACTION_LEECH, mw)
            self.leech_action.setShortcut(leech_seq)
            self.leech_action.triggered.connect(lambda *args: self.handle_input_action(Config.LEECH_ACTIONS))
            mw.stateShortcuts.append(leech_shortcut)

            unleech_seq = QKeySequence(self.toolkit_config[Config.SHORTCUT_OPTIONS][Config.UNLEECH_SHORTCUT])
            unleech_shortcut = QShortcut(
                unleech_seq,
                mw,
                lambda *args: self.handle_input_action(Config.UN_LEECH_ACTIONS)
            )

            self.unleech_action = aqt.qt.QAction(String.REVIEWER_ACTION_UNLEECH, mw)
            self.unleech_action.setShortcut(unleech_seq)
            self.unleech_action.triggered.connect(lambda *args: self.handle_input_action(Config.UN_LEECH_ACTIONS))
            mw.stateShortcuts.append(unleech_shortcut)

    def load_options(self, did: DeckId = None):
        """
        Loads options to UI elements and config-based actions, as well as appends hooks to the initialized reviewer.

        :param did: deck id used for determining config values
        """
        self.did = did if did else self.did

        deck_conf_dict = _try_get_config_dict_for_did(self.did)
        self.max_fails = deck_conf_dict['lapse']['leechFails']

        global_conf = LeechToolkitConfigManager(mw).config
        self.toolkit_config = merge_fields(global_conf.get(str(deck_conf_dict['id']), {}), global_conf)

        self.append_marker_html()
        self.append_lrr_html()
        self.append_hooks()

    def refresh_if_needed(self, changes: aqt.reviewer.OpChanges):
        """
        Function call to update the current window based on whether cards/schedules were changed.

        :param changes: OpChanges object to reference for schedule/card/note changes.
        """
        self.reviewer.op_executed(changes=changes, handler=self, focused=True)
        if not self.reviewer.refresh_if_needed():
            self.update_card_html()

    def marker_html(self):
        out_html = MARKER_HTML_TEMP
        marker_conf = self.toolkit_config[Config.MARKER_OPTIONS]

        if os.path.isfile(f'{ROOT_DIR}\\marker_html.html'):
            with open(f'{ROOT_DIR}\\marker_html.html', 'r') as f:
                out_html = f.read()

        marker_float = MARKER_POS_STYLES[marker_conf[Config.MARKER_POSITION]]

        out_html = out_html \
            .replace('marker_color', marker_conf[Config.LEECH_COLOR]) \
            .replace('marker_float', marker_float) \
            .replace('marker_text', marker_conf[Config.MARKER_TEXT])

        return out_html

    def append_marker_html(self):
        """
        Appends a leech marker to the review window's html.
        """

        self.content.body += self.marker_html()

    def append_lrr_html(self):
        html_to_add = f"""
            <div style="position: fixed; bottom: 0; left: 0; right: 0; width: 320px; margin: auto">
                <div id="{LRR_HTML_ID}"
                    style="background: red;
                    color: black;
                    display: none"
                >
                </div>
            </div>
        """

        self.content.body += html_to_add

    def hide_lrr_html(self):
        mw.web.eval(f'document.getElementById("{LRR_HTML_ID}").style.display = "none";')

    def show_lrr_html(self, ratio):
        lrr_text = f"LR ratio: {ratio}"
        mw.web.eval(f'document.getElementById("{LRR_HTML_ID}").style.display = "block";')
        mw.web.eval(f'document.getElementById("{LRR_HTML_ID}").textContent = "{lrr_text}";')

    def append_hooks(self):
        """
        Appends hooks to the current reviewer.
        """
        from aqt.gui_hooks import (
            reviewer_did_show_question,
            reviewer_did_show_answer,
            reviewer_did_answer_card,
            reviewer_will_end,
            reviewer_will_show_context_menu,
        )
        if CURRENT_ANKI_VER > ANKI_LEGACY_VER and mw.col.v3_scheduler():
            reviewer_did_answer_card.append(self.on_answer_v3)
        else:
            from anki.hooks import card_did_leech
            card_did_leech.append(self.save_leech)
            reviewer_did_answer_card.append(self.on_answer)

        reviewer_did_show_question.append(self.on_show_front)
        reviewer_did_show_answer.append(self.on_show_back)
        reviewer_will_show_context_menu.append(self.append_context_menu)

        reviewer_will_end.append(self.remove_hooks)

    def save_leech(self, card: anki.cards.Card):
        """
        Appends a temporary, custom leech attribute to the selected card.

        :param card: card object to add the attribute to
        """
        self.leeched_cids.add(card.id)
        # setattr(card, was_leech_attr, True)

    def on_answer_v3(self, reviewer: aqt.reviewer.Reviewer, card: anki.cards.Card, ease):
        rating = aqt.reviewer.V3CardInfo.rating_from_ease(ease)

        if CURRENT_ANKI_VER >= 55:
            states = reviewer.get_scheduling_states()
        else:
            states = reviewer.get_next_states()

        answer = card.col.sched.build_answer(
            card=self.card, states=states, rating=rating
        )

        if card.col.sched.state_is_leech(answer.new_state):
            self.save_leech(card)

        self.on_answer(reviewer, card, ease)
        # Handle card overwrites for reviewer (missing due to executed actions, potentially)
        if not reviewer.card:
            reviewer.card = card

    def append_context_menu(self, webview: AnkiWebView, menu: aqt.qt.QMenu):
        for action in menu.actions():
            if action.text() in (String.REVIEWER_ACTION_LEECH, String.REVIEWER_ACTION_UNLEECH):
                menu.removeAction(action)
        menu.addSeparator()
        menu.addAction(self.leech_action)
        menu.addAction(self.unleech_action)

    def remove_hooks(self):
        try:
            gui_hooks.reviewer_did_answer_card.remove(self.on_answer_v3)
            if not (CURRENT_ANKI_VER > ANKI_LEGACY_VER and mw.col.v3_scheduler()):
                hooks.card_did_leech.remove(self.save_leech)
        except NameError:
            print(ErrorMsg.ACTION_MANAGER_NOT_DEFINED)

        gui_hooks.reviewer_did_show_question.remove(self.on_show_front)
        gui_hooks.reviewer_did_show_answer.remove(self.on_show_back)
        gui_hooks.reviewer_did_answer_card.remove(self.on_answer)

    def handle_card_updates(self, card: anki.cards.Card, update_callback, undo_msg=None):
        current_data = {
            'queue': card.queue.real,
            'due': card.due.real,
            'lapses': card.lapses,
            'fields': card.note().joined_fields() if CURRENT_ANKI_VER > ANKI_LEGACY_VER else card.note().joinedFields(),
            'tags': card.note().tags,
        }
        updated_card = update_callback()
        updated_data = {
            'queue': updated_card.queue.real,
            'due': updated_card.due.real,
            'lapses': updated_card.lapses,
            'fields': card.note().joined_fields() if CURRENT_ANKI_VER > ANKI_LEGACY_VER else card.note().joinedFields(),
            'tags': updated_card.note().tags,
        }

        if current_data != updated_data:
            if CURRENT_ANKI_VER < ANKI_UNDO_UPDATE_VER:
                updated_card.flush()
                updated_card.note().flush()
                # Let Anki handle undo status updates
                mw.checkpoint(undo_msg)
                mw.reset()
            else:
                def push_updates():
                    if undo_msg:
                        entry = self.reviewer.mw.col.add_custom_undo_entry(undo_msg)
                    else:
                        entry = mw.col.undo_status().last_step

                    self.reviewer.mw.col.update_card(updated_card)
                    # If entry was null, set it to this last update
                    entry = mw.col.undo_status().last_step if not entry else entry
                    self.reviewer.mw.col.update_note(updated_card.note())

                    try:
                        if not isinstance(entry, int):
                            self.reviewer.mw.col.add_custom_undo_entry(undo_msg)
                        entry = 0 if not isinstance(entry, int) else entry
                        changes = self.reviewer.mw.col.merge_undo_entries(entry)
                        self.refresh_if_needed(changes)
                    except InvalidInput:
                        showInfo(ErrorMsg.ERROR_TRACEBACK)

                if undo_msg or mw.col.v3_scheduler():
                    push_updates()
                else:
                    # Let reviewer handle entry and flush updates pre-logging, instead.
                    updated_card.flush()

                    current_field_tags = (current_data['fields'], current_data['tags'])
                    updated_field_tags = (updated_data['fields'], updated_data['tags'])

                    if current_field_tags != updated_field_tags:
                        updated_card.note().flush()

            return updated_card

        return card

    def handle_input_action(self, action_type: str):
        """
        Function for handling action calls via shortcuts/context menu actions.

        :param action_type: action type string to use as a reference for the undo entry actions to take
        """
        msg = String.ENTRY_LEECH_ACTIONS if action_type == Config.LEECH_ACTIONS else String.ENTRY_UNLEECH_ACTIONS
        updated_card = mw.col.get_card(self.card.id)

        def perform_actions():
            card = handle_actions(updated_card, self.toolkit_config, action_type)

            if action_type == Config.LEECH_ACTIONS:
                card.note().add_tag(LEECH_TAG)
                if self.toolkit_config[Config.TOAST_ENABLED]:
                    tooltip(String.TIP_LEECHED_TEMPLATE.format(1))

            elif action_type == Config.UN_LEECH_ACTIONS:
                card.note().remove_tag(LEECH_TAG)
                if self.toolkit_config[Config.TOAST_ENABLED]:
                    tooltip(String.TIP_UNLEECHED_TEMPLATE.format(1))

            return card

        self.handle_card_updates(self.card, perform_actions, msg)

    def on_show_back(self, card: anki.cards.Card):
        """
        Updates the current card, leech marker, and view state to back values.

        :param card: referenced card
        """
        self.on_front = False
        self.card = card
        if self.toolkit_config[Config.REVERSE_OPTIONS][Config.REVERSE_ENABLED]:
            setattr(self.card, PREV_TYPE_ATTR, self.card.type)
        self.update_card_html()

    def on_show_front(self, card: anki.cards.Card):
        """
        Updates the current card, leech marker, and view state to front values.

        :param card: referenced card
        """
        self.on_front = True
        self.card = card
        self.update_card_html()

    def on_answer(self, context: aqt.reviewer.Reviewer, card: anki.cards.Card, ease: int):
        """
        Handles updates after answering cards.

        :param context: unused Reviewer object
        :param card: referenced card
        :param ease: value of the answer given
        """

        if CURRENT_ANKI_VER <= ANKI_LEGACY_VER:
            card.col.get_card = lambda cid: anki.cards.Card(mw.col, cid)

        def handle_card_answer():
            updated_card = card.col.get_card(card.id)

            if hasattr(card, PREV_TYPE_ATTR):
                updated_card = handle_reverse(self.toolkit_config, card, ease, card.__getattribute__(PREV_TYPE_ATTR))
                delattr(card, PREV_TYPE_ATTR)

            if card.id in self.leeched_cids:
                updated_card = handle_actions(card, self.toolkit_config, Config.LEECH_ACTIONS, reload=False)
                self.leeched_cids.remove(card.id)

            return updated_card

        self.handle_card_updates(card, handle_card_answer)

    def update_card_html(self):
        """
        Update marker style/visibility based on config options and card attributes.
        Renamed from update_marker() as it will also do stuff for lapse review ratio features
        """
        marker_conf = self.toolkit_config[Config.MARKER_OPTIONS]
        lrr_conf = self.toolkit_config[Config.LAPSE_REVIEW_RATIO_OPTIONS]
        self.hide_lrr_html()
        show_marker(False)

        if marker_conf[Config.SHOW_LEECH_MARKER]:
            only_show_on_back = marker_conf[Config.ONLY_SHOW_BACK_MARKER]
            is_review = self.card.type == anki.cards.CARD_TYPE_REV
            almost_leech = \
                is_review and self.card.lapses + marker_conf[Config.ALMOST_DISTANCE] >= self.max_fails

            if (not self.on_front and only_show_on_back) or not only_show_on_back:
                if CURRENT_ANKI_VER <= ANKI_LEGACY_VER:
                    self.card.note().has_tag = lambda tag: tag.lower() in [t.lower() for t in self.card.note().tags]

                if self.card.note().has_tag(LEECH_TAG):
                    set_marker_color(marker_conf[Config.LEECH_COLOR])
                    show_marker(True)
                elif marker_conf[Config.USE_ALMOST_MARKER] and almost_leech:
                    set_marker_color(marker_conf[Config.ALMOST_COLOR])
                    show_marker(True)

        if lrr_conf[Config.LAPSE_REVIEW_RATIO_FEATURE_ENABLED]:
            threshold = float(lrr_conf[Config.LAPSE_REVIEW_RATIO_THRESHOLD])
            ratio = calculations.lr_ratio_for_card(mw, self.card.id)
            if (ratio > threshold):
                self.show_lrr_html(ratio)
