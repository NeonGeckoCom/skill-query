# NEON AI (TM) SOFTWARE, Software Development Kit & Application Framework
# All trademark and other rights reserved by their respective owners
# Copyright 2008-2022 Neongecko.com Inc.
# Contributors: Daniel McKnight, Guy Daniels, Elon Gasper, Richard Leeds,
# Regina Bloomstine, Casimiro Ferreira, Andrii Pernatii, Kirill Hrymailo
# BSD-3 License
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
# 3. Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from this
#    software without specific prior written permission.
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
# THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
# CONTRIBUTORS  BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA,
# OR PROFITS;  OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE,  EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
# Copyright 2018 Mycroft AI Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from neon_utils.skills.neon_fallback_skill import NeonFallbackSkill
from mycroft.messagebus.message import Message
from threading import Lock, Event

EXTENSION_TIME = 10
HIGHEST_POSSIBLE_SCORE = 1
MODE_EXTENSION_TIME = {"quick": 5,
                       "default": EXTENSION_TIME}


class QuestionsAnswersSkill(NeonFallbackSkill):
    def __init__(self):
        super().__init__()
        self.query_replies = {}  # cache of received replies
        self.query_extensions = {}  # maintains query timeout extensions
        self.lock = Lock()
        self.timeout_time = None
        self.waiting = Event()
        self.answered = False

    def initialize(self):
        self.add_event('question:query.response',
                       self.handle_query_response)
        self.register_fallback(self.handle_question, 5)

    def handle_question(self, message):
        """ Send the phrase to the CommonQuerySkills and prepare for handling
            the replies.
        """
        # Check if we are certain Neon should respond to this
        if self.neon_in_request(message) or len(str(message.data.get("utterance")).split()) >= 4:
            self.answered = False
            context = message.context
            utt = message.data.get('utterance')
            # self.enclosure.mouth_think()
            utt = utt.lower().lstrip("neon ")
            self.query_replies[utt] = []
            self.query_extensions[utt] = []
            self.log.info('Searching for {}'.format(utt))
            skill_mode = self.user_config.get('response_mode', {}).get('speed_mode', 'default')
            extension_time = MODE_EXTENSION_TIME.get(skill_mode) or EXTENSION_TIME
            self.timeout_time = extension_time
            # Send the query to anyone listening for them
            self.waiting.clear()
            self.bus.emit(message.forward('question:query', data={'phrase': utt}))
            self.waiting.wait(self.timeout_time)
            self.waiting.clear()

            self._query_timeout(Message(msg_type="neon.query_timeout", data={'phrase': utt}, context=context))
            return self.answered
        return True

    def handle_query_response(self, message):
        with self.lock:
            search_phrase = message.data['phrase']
            skill_id = message.data['skill_id']
            searching = message.data.get('searching')
            answer = message.data.get('answer')

            # Manage requests for time to complete searches
            if searching:
                # TODO: Perhaps block multiple extensions?
                if (search_phrase in self.query_extensions and
                        skill_id not in self.query_extensions[search_phrase]):
                    self.query_extensions[search_phrase].append(skill_id)
            elif search_phrase in self.query_extensions:
                # Search complete, don't wait on this skill any longer
                if answer and search_phrase in self.query_replies:
                    self.log.info('Answer from {}'.format(skill_id))
                    self.query_replies[search_phrase].append(message.data)
                    # if the confidence score is maximal, there is no need to further search for a better response
                    if message.data.get('conf') == HIGHEST_POSSIBLE_SCORE:
                        self.waiting.set()
                # Remove the skill from list of extensions
                if skill_id in self.query_extensions[search_phrase]:
                    self.query_extensions[search_phrase].remove(skill_id)
                    # if the list of extensions is empty, there are no skills left to wait for
                    if not self.query_extensions[search_phrase]:
                        self.waiting.set()
            else:
                self.log.warning('{} Answered too slowly,'
                                 'will be ignored.'.format(skill_id))

    def _query_timeout(self, message):
        # Prevent any late-comers from retriggering this query handler
        with self.lock:
            self.log.info('Timeout occurred check responses')
            search_phrase = message.data['phrase']
            if search_phrase in self.query_extensions:
                self.query_extensions[search_phrase] = []

            # Look at any replies that arrived before the timeout
            # Find response(s) with the highest confidence
            best = None
            ties = []
            if search_phrase in self.query_replies:
                for handler in self.query_replies[search_phrase]:
                    if not best or handler['conf'] > best['conf']:
                        best = handler
                        ties = []
                    elif handler['conf'] == best['conf']:
                        ties.append(handler)

            if best:
                if ties:
                    # TODO: Ask user to pick between ties or do it automagically
                    pass

                # invoke best match
                self.speak(best['answer'], message=message)
                self.log.info('Handling with: ' + str(best['skill_id']))
                self.bus.emit(message.forward('question:action',
                                              data={'skill_id': best['skill_id'],
                                                    'phrase': search_phrase,
                                                    'callback_data':
                                                        best.get('callback_data')}))
                self.answered = True
            else:
                self.answered = False
            if search_phrase in self.query_replies:
                del self.query_replies[search_phrase]
            if search_phrase in self.query_extensions:
                del self.query_extensions[search_phrase]


def create_skill():
    return QuestionsAnswersSkill()
