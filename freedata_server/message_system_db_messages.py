from freedata_server.message_system_db_manager import DatabaseManager
from freedata_server.message_system_db_attachments import DatabaseManagerAttachments
from freedata_server.message_system_db_model import Attachment, MessageAttachment, Status, P2PMessage
from freedata_server.message_system_db_station import DatabaseManagerStations
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta, timezone
import csv
import os
from freedata_server.exceptions import MessageStatusError


# a spreadsheet runs a cell that starts with one of these as a formula
CSV_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_text(value):
    """Text for a message log cell; text from other stations that would start
    a formula gets a leading apostrophe, so a spreadsheet shows it as text."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(CSV_FORMULA_START) else text


def status_name_of(message):
    return message.status.name if message.status else None


class DatabaseManagerMessages(DatabaseManager):
    """Manages database operations for P2P messages.

    This class extends the DatabaseManager and provides methods for
    managing P2P messages in the database, including adding, retrieving,
    updating, deleting messages, handling attachments, and managing
    message statuses.
    """

    # P2PMessage.priority of a new or retransmitted message (the model's default)
    DEFAULT_PRIORITY = 10
    # the order queued messages go out in: highest priority first, then oldest
    QUEUE_ORDER = (P2PMessage.priority.desc(), P2PMessage.timestamp)

    def __init__(self, ctx):
        """Initializes DatabaseManagerMessages.

        Args:
            ctx (EventManager): The event manager instance.
        """
        super().__init__(ctx)

        self.attachments_manager = DatabaseManagerAttachments(self.ctx)
        self.stations_manager = DatabaseManagerStations(self.ctx)

    def add_message(
        self,
        message_data,
        statistics,
        direction="receive",
        status=None,
        is_read=True,
        frequency=None,
        encrypted=False,
    ):
        """Adds a new P2P message to the database.

        This method adds a new P2P message record to the database,
        including its associated attachments. It handles station creation,
        status management, timestamp parsing, and attachment processing.
        It also logs the addition and triggers a database change event.

        Args:
            message_data (dict): A dictionary containing the message data.
            statistics (dict): A dictionary containing message statistics.
            direction (str, optional): The direction of the message ('send' or 'receive'). Defaults to 'receive'.
            status (str, optional): The status of the message. Defaults to None.
            is_read (bool, optional): Whether the message has been read. Defaults to True.
            frequency (float, optional): The frequency of the message. Defaults to None.
            encrypted (bool, optional): Whether the message was received encrypted. Defaults to False.

        Returns:
            str or None: The message ID if successfully added, None otherwise.
        """
        session = self.get_thread_scoped_session()
        try:
            # Create and add the origin and destination Stations
            origin = self.stations_manager.get_or_create_station(message_data["origin"], session)
            destination = self.stations_manager.get_or_create_station(message_data["destination"], session)

            # Create and add Status if provided
            if status:
                status = self.get_or_create_status(session, status)

            # Parse the timestamp from the message ID (origin_destination_isotimestamp).
            # Fall back to the current time for IDs that don't follow this format.
            try:
                timestamp = datetime.fromisoformat(message_data["id"].rsplit("_", 1)[1])
            except (IndexError, ValueError, TypeError, AttributeError):
                self.log(
                    f"Could not parse timestamp from message ID {message_data.get('id')}, using current time",
                    isWarning=True,
                )
                timestamp = datetime.now(timezone.utc)

            if frequency and frequency not in ["---"]:
                statistics["frequency"] = frequency

            # Create the P2PMessage instance
            new_message = P2PMessage(
                id=message_data["id"],
                origin_callsign=origin.callsign,
                destination_callsign=destination.callsign,
                body=message_data["body"],
                timestamp=timestamp,
                direction=direction,
                status_id=status.id if status else None,
                is_read=is_read,
                attempt=0,
                statistics=statistics,
                encrypted=encrypted,
            )

            session.add(new_message)

            # Process and add attachments
            if "attachments" in message_data:
                for attachment_data in message_data["attachments"]:
                    self.attachments_manager.add_attachment(session, new_message, attachment_data)

            session.commit()
            self.log(f"Added data to database: {new_message.id}")
            self.ctx.event_manager.freedata_message_db_change(message_id=new_message.id)
            return new_message.id
        except IntegrityError as _:
            session.rollback()  # Roll back the session to a clean state
            self.log(
                f"Message with ID {message_data['id']} already exists in the database.",
                isWarning=True,
            )
            return None

        except Exception as e:
            session.rollback()
            self.log(f"error adding new message to database with error: {e}", isWarning=True)
            return None  # Return None to indicate an error

        finally:
            session.remove()

    def get_all_messages(self, filters=None):
        """Retrieves all P2P messages from the database, optionally filtered.

        This method retrieves all P2P messages from the database. It
        supports optional filtering based on criteria such as message ID,
        callsign (origin, via, or destination), direction, etc. The
        results are returned as a list of dictionaries, each representing
        a message. It handles database errors and logs appropriate
        messages.

        Args:
            filters (dict, optional): A dictionary of filter criteria.
                Available filters include 'id', 'callsign',
                'origin_callsign', 'via_callsign',
                'destination_callsign', and 'direction'. Defaults to
                None.

        Returns:
            list: A list of dictionaries, each representing a message, or
            an empty list if an error occurs or no messages match the
            filters.
        """
        session = self.get_thread_scoped_session()
        try:
            query = session.query(P2PMessage)

            if filters:
                for key, value in filters.items():
                    if key == "id":
                        query = query.filter(P2PMessage.id == value)
                    elif key == "callsign":
                        query = query.filter(
                            (P2PMessage.origin_callsign.contains(value))
                            | (P2PMessage.via_callsign.contains(value))
                            | (P2PMessage.destination_callsign.contains(value))
                        )
                    elif key in [
                        "origin_callsign",
                        "via_callsign",
                        "destination_callsign",
                        "direction",
                    ]:
                        query = query.filter(getattr(P2PMessage, key).contains(value))

            messages = query.all()
            return [message.to_dict() for message in messages]

        except Exception as e:
            self.log(f"error fetching database messages with error: {e}", isWarning=True)
            self.log("---> please delete or update existing database", isWarning=True)

            return []

        finally:
            session.remove()

    def get_all_messages_json(self, filters=None):
        """Retrieves all P2P messages as a JSON string, with a header.

        This method retrieves all messages, optionally filtered, and
        returns them as a JSON string. It includes a header containing the
        total number of messages. It calls get_all_messages to fetch the
        messages and then formats them as a JSON string.

        Args:
            filters (dict, optional): Filter criteria for messages. See
                get_all_messages for details. Defaults to None.

        Returns:
            dict: A dictionary containing the total number of messages and
            the messages themselves, formatted for JSON serialization.
        """
        messages_dict = self.get_all_messages(filters)
        messages_with_header = {"total_messages": len(messages_dict), "messages": messages_dict}
        return messages_with_header

    MESSAGE_LOG_HEADER = ("Date/time (UTC)", "Sender", "Receiver", "Message", "Attachments")

    def write_message_log_csv(self, directory=None):
        """Writes all P2P messages, oldest first, to a new CSV file.

        One row per message: date and time (UTC), sender, receiver, message
        text and the file names of its attachments (separated by "; ",
        empty when there are none). The file is UTF-8 with a byte order
        mark, so spreadsheets read non-ASCII text right. The separator is a
        semicolon: Excel splits a double-clicked CSV at the Windows list
        separator, which is ";" wherever the decimal mark is a comma (for
        example Finnish and German settings). Quoting is as in RFC 4180, so
        semicolons, quotes and line breaks in a message stay in their cell.

        Args:
            directory (str, optional): Where to write the file. Defaults to
                the directory of the message database.

        Returns:
            dict: status, file_name, file_path and total_messages.
        """
        if directory is None:
            directory = os.path.dirname(os.path.abspath(self.get_database_path()))

        session = self.get_thread_scoped_session()
        try:
            messages = (
                session
                .query(
                    P2PMessage.id,
                    P2PMessage.timestamp,
                    P2PMessage.origin_callsign,
                    P2PMessage.destination_callsign,
                    P2PMessage.body,
                )
                .order_by(P2PMessage.timestamp, P2PMessage.id)
                .all()
            )
            attachment_names = {}
            # names only: the attachment data can be large
            for message_id, name in (
                session
                .query(MessageAttachment.message_id, Attachment.name)
                .join(Attachment, MessageAttachment.attachment_id == Attachment.id)
                .order_by(Attachment.id)
            ):
                attachment_names.setdefault(message_id, []).append(name or "")
        finally:
            session.remove()

        os.makedirs(directory, exist_ok=True)
        stem = f"freedata-message-log_{datetime.now():%Y%m%d-%H%M%S}"
        file_name = f"{stem}.csv"
        number = 1
        while True:
            file_path = os.path.join(directory, file_name)
            try:
                csv_file = open(file_path, "x", encoding="utf-8-sig", newline="")
                break
            except FileExistsError:
                number += 1
                file_name = f"{stem}_{number}.csv"

        with csv_file:
            writer = csv.writer(csv_file, delimiter=";")
            writer.writerow(self.MESSAGE_LOG_HEADER)
            for message_id, timestamp, origin, destination, body in messages:
                writer.writerow((
                    timestamp.strftime("%Y-%m-%d %H:%M:%S") if timestamp else "",
                    csv_text(origin),
                    csv_text(destination),
                    csv_text(body),
                    csv_text("; ".join(attachment_names.get(message_id, []))),
                ))

        self.log(f"Wrote {len(messages)} messages to {file_path}")
        return {
            "status": "success",
            "file_name": file_name,
            "file_path": file_path,
            "total_messages": len(messages),
        }

    def get_message_by_id(self, message_id):
        """Retrieves a P2P message by its ID.

        This method retrieves a specific P2P message from the database
        based on its ID. It returns the message data as a dictionary if
        found, or None if not found or if an error occurs. It handles
        database errors and logs appropriate messages.

        Args:
            message_id (str): The ID of the message to retrieve.

        Returns:
            dict or None: The message data as a dictionary if found, None
            otherwise.
        """
        session = self.get_thread_scoped_session()
        try:
            message = session.query(P2PMessage).filter_by(id=message_id).first()
            if message:
                return message.to_dict()
            else:
                return None
        except Exception as e:
            self.log(f"Error fetching message with ID {message_id}: {e}", isWarning=True)
            return None
        finally:
            session.remove()

    def get_message_by_id_adif(self, message_id):
        """
        Fetches a message by ID and returns it in ADIF format.

        Parameters:
        message_id (str): The ID of the message to retrieve.

        Returns:
        str: The ADIF formatted message, or None if the message is not found.
        """
        message_dict = self.get_message_by_id(message_id)
        if message_dict:
            try:
                # Extract fields from the message dictionary
                origin = message_dict.get("origin", "")
                destination = message_dict.get("destination", "")
                timestamp = message_dict.get("timestamp", "")
                direction = message_dict.get("direction", "").lower()  # Ensure case insensitivity
                _status = message_dict.get("status", "")
                statistics = message_dict.get("statistics", {})

                # Determine the CALL based on the direction
                if direction == "receive":
                    call = origin.split("-")[0]  # Take the part before the "-" if it exists
                else:
                    call = destination.split("-")[0]  # Take the part before the "-" if it exists

                # Fetch station info
                origin_info = self.stations_manager.get_station(origin)

                # Parse the timestamp for QSO date and time, strip fractional seconds
                timestamp_clean = timestamp.split(".")[0]  # Remove fractional seconds
                qso_date = timestamp_clean.split("T")[0].replace("-", "")  # Extract the date part and remove hyphens

                # Extract the time and format it as HHMMSS
                time_on = datetime.strptime(timestamp_clean.split("T")[1], "%H:%M:%S").strftime("%H%M%S")

                # Calculate TIME_OFF by adding duration to TIME_ON
                duration = statistics.get("duration", 0.0)  # Duration in seconds
                time_on_obj = datetime.strptime(
                    timestamp_clean.split("T")[1], "%H:%M:%S"
                )  # Parse time_on as a datetime object
                time_off_obj = time_on_obj + timedelta(seconds=duration)
                time_off = time_off_obj.strftime("%H%M%S")  # Format time_off back to HHMMSS format

                # Set ADIF Fields
                mode = "DYNAMIC"
                submode = "FREEDATA"
                comment = "QSO via FreeDATA RF-Chat"

                # Gridsquare handling
                if origin_info and "location" in origin_info and origin_info["location"] is not None:
                    grid = origin_info["location"].get("gridsquare", "")
                else:
                    grid = ""

                # Extract and adjust the frequency (Hz to MHz)
                frequency_hz = statistics.get("frequency")  # Get the frequency, may be None
                if frequency_hz is not None:
                    try:
                        # Convert frequency_hz to float if it's a string
                        frequency_value = float(frequency_hz)
                    except ValueError:
                        frequency_value = 0.0
                    frequency_mhz = round(frequency_value / 1_000_000, 3)  # Convert Hz to MHz
                    frequency_str = str(frequency_mhz)
                else:
                    frequency_str = "14.000000"  # Default if frequency is missing

                # Construct the ADIF message
                adif_fields = [
                    f"<CALL:{len(call)}>{call}",
                    f"<QSO_DATE:{len(qso_date)}>{qso_date}",
                    f"<TIME_ON:{len(time_on)}>{time_on}",
                    f"<TIME_OFF:{len(time_off)}>{time_off}",  # Include TIME_OFF
                    f"<FREQ:{len(frequency_str)}>{frequency_str}",  # Frequency in MHz
                    f"<MODE:{len(mode)}>{mode}",
                    f"<SUBMODE:{len(submode)}>{submode}",
                    f"<COMMENT:{len(comment)}>{comment}",
                    f"<GRIDSQUARE:{len(grid)}>{grid}",
                    # f"<DIRECTION:{len(direction)}>{direction}",
                    # f"<STATUS:{len(status)}>{status}",
                    # f"<DURATION:{len(str(duration))}>{duration}",
                    "<EOR>",
                ]

                return "".join(adif_fields)

            except Exception as e:
                self.log(f"Error converting message {message_id} to ADIF: {e}", isWarning=True)
                return None
        else:
            self.log(f"Message with ID {message_id} not found.", isWarning=True)
            return None

    def delete_message(self, message_id, allow_queued=False):
        """Deletes a P2P message from the database.

        This method deletes a P2P message and its associated attachments
        from the database. It first deletes the attachment links (and the
        attachments themselves if they are not linked to other
        messages). Then, it deletes the message record and logs the
        deletion, triggering a database change event.

        Args:
            message_id (str): The ID of the message to delete.
            allow_queued (bool): Also delete a queued message (the queue tab,
                which claims it first so it is not being sent meanwhile). A
                transmitting message is never deleted.

        Returns:
            dict: A dictionary indicating the status of the deletion
            ('success' or 'failure') and a corresponding message.
        """

        session = self.get_thread_scoped_session()
        try:
            message = session.query(P2PMessage).filter_by(id=message_id).first()
            if message:
                # Check the guard before touching attachments, so a refused delete keeps them.
                # (Don't use to_dict() here: it would load the attachment links into this session.)
                status_name = message.status.name if message.status else None
                if status_name == "transmitting" or (status_name == "queued" and not allow_queued):
                    raise MessageStatusError("Message is queued or transmitting")

                # Delete attachment links associated with this message.
                # This call will check each attachment link:
                # - If the attachment is used by other messages, only the link is removed.
                # - If the attachment is solely linked to this message, the attachment record is deleted.
                self.attachments_manager.delete_attachments_by_message_id(message_id)

                session.delete(message)
                session.commit()

                self.log(f"Deleted: {message_id}")
                self.ctx.event_manager.freedata_message_db_change(message_id=message_id)
                return {"status": "success", "message": f"Message {message_id} deleted"}
            else:
                return {"status": "failure", "message": "Message not found"}
        except MessageStatusError as e:
            session.rollback()
            self.log(f"Error deleting message with ID {message_id}: {e}", isWarning=True)
            return {"status": "failure", "message": str(e)}
        except Exception as e:
            session.rollback()
            self.log(f"Error deleting message with ID {message_id}: {e}", isWarning=True)
            return {"status": "failure", "message": "error deleting message"}

        finally:
            session.remove()

    def update_message(self, message_id, update_data, frequency=None):
        """Updates a P2P message in the database.

        This method updates an existing P2P message in the database with
        the provided data. It allows updating various fields of the
        message, including body, status, statistics, read status, attempt
        count, and priority. It handles database errors, performs
        rollbacks if necessary, and triggers a database change event upon
        successful update.

        Args:
            message_id (str): The ID of the message to update.
            update_data (dict): A dictionary containing the fields to
                update and their new values.
            frequency (float, optional): The frequency to update in
                statistics. Defaults to None.

        Returns:
            dict: A dictionary indicating the status of the update
            ('success' or 'failure') and a corresponding message.
        """
        session = self.get_thread_scoped_session()
        try:
            message = session.query(P2PMessage).filter_by(id=message_id).first()
            if message:
                # Update fields of the message as per update_data
                for key, value in update_data.items():
                    if key == "status":
                        setattr(message, key, self.get_or_create_status(session, value))
                    elif key == "statistics":
                        statistics = value
                        if frequency and frequency not in ["---"]:
                            statistics["frequency"] = frequency
                        setattr(message, key, statistics)
                    elif key in ["body", "is_read", "attempt", "priority", "encrypted"]:
                        setattr(message, key, value)

                session.commit()
                self.log(f"Updated: {message_id}")
                self.ctx.event_manager.freedata_message_db_change(message_id=message_id)
                return {"status": "success", "message": f"Message {message_id} updated"}
            else:
                return {"status": "failure", "message": "Message not found"}

        except Exception as e:
            session.rollback()
            self.log(f"Error updating message with ID {message_id}: {e}", isWarning=True)
            return {"status": "failure", "message": "error updating message"}

        finally:
            session.remove()

    def get_first_queued_message(self):
        """Retrieves the first queued P2P message from the database.

        This method retrieves the next queued message to send: the highest
        priority first (send_last lowers it), then the oldest by timestamp. It returns the message data as a
        dictionary if found, or None if no queued messages are found or
        an error occurs. It handles cases where the "queued" status is
        not found and logs appropriate messages.

        Returns:
            dict or None: The data of the first queued message as a
            dictionary, or None if no queued messages are found or if an
            error occurs.
        """
        session = self.get_thread_scoped_session()
        try:
            # Find the status object for "queued"
            queued_status = session.query(Status).filter_by(name="queued").first()
            if queued_status:
                # Query for the first (oldest) message with status "queued"
                message = session.query(P2PMessage).filter_by(status=queued_status).order_by(*self.QUEUE_ORDER).first()
                if message:
                    self.log(f"Found queued message with ID {message.id}")
                    return message.to_dict()
                else:
                    return None
            else:
                self.log("Queued status not found", isWarning=True)
                return None
        except Exception as e:
            self.log(f"Error fetching the first queued message: {e}", isWarning=True)
            return None
        finally:
            session.remove()

    def get_queue(self):
        """Returns the messages waiting to be sent, in the order they go out.

        The message being sent ('transmitting') comes first, then the 'queued'
        ones in get_first_queued_message's order. Attachments are listed by
        name and type only, without their data.

        Returns:
            list: One dict per message, with its 1-based 'position'.
        """
        session = self.get_thread_scoped_session()
        try:
            rows = []
            for status_name in ("transmitting", "queued"):
                rows += (
                    session
                    .query(P2PMessage)
                    .join(Status, P2PMessage.status_id == Status.id)
                    .filter(Status.name == status_name, P2PMessage.direction == "transmit")
                    .order_by(*self.QUEUE_ORDER)
                    .all()
                )
            return [
                {
                    "position": position,
                    "id": message.id,
                    "timestamp": message.timestamp.isoformat() if message.timestamp else None,
                    "destination": message.destination_callsign,
                    "body": message.body,
                    "attachments": [
                        {"name": link.attachment.name, "type": link.attachment.data_type}
                        for link in message.message_attachments
                    ],
                    "status": status_name_of(message),
                    "attempt": message.attempt,
                    "priority": message.priority,
                    "encrypted": bool(message.encrypted),
                }
                for position, message in enumerate(rows, 1)
            ]
        except Exception as e:
            self.log(f"Error reading the message queue: {e}", isWarning=True)
            return []
        finally:
            session.remove()

    def send_last(self, message_id):
        """Moves a queued message behind every other queued message.

        Its priority is set below the lowest of the others, so it also goes
        after messages queued later (those get DEFAULT_PRIORITY). A manual
        retransmit gives it DEFAULT_PRIORITY again.

        Returns:
            dict: 'status' 'success' or 'failure' and a 'message'.
        """
        session = self.get_thread_scoped_session()
        try:
            message = session.query(P2PMessage).filter_by(id=message_id).first()
            if not message:
                return {"status": "failure", "message": "Message not found"}
            if status_name_of(message) != "queued":
                return {"status": "failure", "message": "Only a queued message can be moved"}
            lowest = (
                session
                .query(func.min(P2PMessage.priority))
                .join(Status, P2PMessage.status_id == Status.id)
                .filter(Status.name == "queued", P2PMessage.id != message_id)
                .scalar()
            )
            if lowest is not None:
                message.priority = min(message.priority or self.DEFAULT_PRIORITY, lowest - 1)
            session.commit()
        except Exception as e:
            session.rollback()
            self.log(f"Error moving message {message_id} to the end of the queue: {e}", isWarning=True)
            return {"status": "failure", "message": "error moving message"}
        finally:
            session.remove()

        self.log(f"Moved to the end of the queue: {message_id}")
        self.ctx.event_manager.freedata_message_db_change(message_id=message_id)
        return {"status": "success", "message": f"Message {message_id} moved to the end of the queue"}

    def abort_unsent_messages(self):
        """Sets every queued or transmitting message to 'aborted' (the Stop button).

        'aborted', not 'failed': set_message_to_queued_for_callsign queues a
        failed message again once its station is heard (MESSAGES.enable_auto_repeat),
        and a stopped message must only go out when it is retransmitted by hand.

        Returns:
            list: The IDs of the aborted messages.
        """
        session = self.get_thread_scoped_session()
        try:
            unsent = (
                session
                .query(P2PMessage)
                .join(Status, P2PMessage.status_id == Status.id)
                .filter(Status.name.in_(["queued", "transmitting"]))
                .all()
            )
            aborted_status = self.get_or_create_status(session, "aborted")
            ids = [message.id for message in unsent]
            for message in unsent:
                message.status_id = aborted_status.id
            session.commit()
        except Exception as e:
            session.rollback()
            self.log(f"Error aborting unsent messages: {e}", isWarning=True)
            return []
        finally:
            session.remove()

        for message_id in ids:
            self.log(f"Aborted: {message_id}")
            self.ctx.event_manager.freedata_message_db_change(message_id=message_id)
        return ids

    def increment_message_attempts(self, message_id, session=None):
        """Increments the attempt count for a message.

        This method increments the attempt count for a given message in
        the database. It handles database sessions and transactions,
        committing changes only if it created the session. It logs
        messages indicating success, failure (message not found), or
        errors during the process.

        Args:
            message_id (str): The ID of the message to update.
            session (scoped_session, optional): An existing database
                session. Defaults to None.
        """
        own_session = False
        if not session:
            session = self.get_thread_scoped_session()
            own_session = True
        try:
            message = session.query(P2PMessage).filter_by(id=message_id).first()
            if message:
                message.attempt += 1
                if own_session:
                    session.commit()
                self.log(f"Incremented attempt count for message {message_id}")
            else:
                self.log(f"Message with ID {message_id} not found")
        except Exception as e:
            if own_session:
                session.rollback()
            self.log(f"An error occurred while incrementing attempts for message {message_id}: {e}")
        finally:
            if own_session:
                session.remove()

    def set_message_to_queued_for_callsign(self, callsign):
        """Sets the first failed message for a callsign to 'queued'.

        This method finds the first message in the database with the given
        destination callsign that has a 'failed' status and fewer than 10
        attempts. If such a message is found, its status is changed to
        'queued', its attempt count is incremented, and the changes are
        committed to the database. It handles cases where the 'failed' or
        'queued' statuses are not found and logs appropriate messages.

        Args:
            callsign (str): The destination callsign of the message.

        Returns:
            dict: A dictionary indicating the status ('success' or
            'failure') and a message describing the outcome.
        """
        session = self.get_thread_scoped_session()
        try:
            # Find the 'failed' status object
            failed_status = session.query(Status).filter_by(name="failed").first()
            # Find the 'queued' status object
            queued_status = session.query(Status).filter_by(name="queued").first()

            # Ensure both statuses are found
            if not failed_status or not queued_status:
                self.log("Failed or queued status not found", isWarning=True)
                return {"status": "failure", "message": "Failed or queued status not found"}

            # Query for messages with the specified callsign, 'failed' status, and fewer than 10 attempts
            message = (
                session
                .query(P2PMessage)
                .filter(P2PMessage.destination_callsign == callsign)
                .filter(P2PMessage.status_id == failed_status.id)
                .filter(P2PMessage.attempt < 10)
                .first()
            )

            if message:
                # Increment attempt count using the existing function
                self.increment_message_attempts(message.id, session)

                message.status_id = queued_status.id
                self.log(f"Set message {message.id} to queued and incremented attempt")

                session.commit()
                return {"status": "success", "message": "message(s) set to queued"}
            else:
                return {"status": "failure", "message": "No eligible messages found"}
        except Exception as e:
            session.rollback()
            self.log(f"An error occurred while setting messages to queued: {e}", isWarning=True)
            return {"status": "failure", "message": "error setting message to queued"}
        finally:
            session.remove()
