(() => {
    const results = [];
    const url = window.location.href.replace(/\/+$/, "");

    const isVisible = (el) => {
        const rect = el.getBoundingClientRect();
        const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
        const viewportWidth = window.innerWidth || document.documentElement.clientWidth;

        // Calculate the visible area of the element
        const visibleTop = Math.max(rect.top, 0);
        const visibleLeft = Math.max(rect.left, 0);
        const visibleBottom = Math.min(rect.bottom, viewportHeight);
        const visibleRight = Math.min(rect.right, viewportWidth);

        // If element is completely outside viewport
        if (visibleTop >= visibleBottom || visibleLeft >= visibleRight) {
            return false;
        }

        const visibleArea = (visibleBottom - visibleTop) * (visibleRight - visibleLeft);
        const totalArea = rect.height * rect.width;

        // Consider visible if at least 50% of the element is in viewport
        return totalArea > 0 && (visibleArea / totalArea) >= 0.5;
    };

    const isRecorded = (el) => {
        return el.getAttribute("__recorded") === "true";
    };

    const setIsRecorded = (el) => {
        el.setAttribute("__recorded", "true");
    };

    const parsePartySize = (text) => {
        // use regex to extract party size
        // input `text` may look like:
        //   - 1 person
        //   - 2 people
        // output is an integer
        const match = text.match(/(\d+) (person|people)/);
        if (match) {
            return parseInt(match[1]);
        }
        return null;
    };

    const parseTimes = (text) => {
        // use regex to extract times
        // input `text` may look like:
        //   - 8:15 PM...8:30 PM...
        // output is an array of strings in HH:mm:ss format
        const times = [];
        const timeMatches = text.matchAll(/(\d{1,2}):(\d{2}) ([APap][Mm])/g);
        for (const timeMatch of timeMatches) {
            const hourStr = timeMatch[1];
            const minuteStr = timeMatch[2];
            const ampm = timeMatch[3].toLowerCase();

            let hours = parseInt(hourStr);
            if (ampm === "pm" && hours !== 12) {
                hours += 12;
            } else if (ampm === "am" && hours === 12) {
                hours = 0;
            }

            const minutes = parseInt(minuteStr);
            const seconds = 0;
            const time = `${hours.toString().padStart(2, "0")}:${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`;
            times.push(time);
        }
        return times;
    };

    const parseDateAndTimes = (text) => {
        // use regex to extract date and times
        // input `text` may look like:
        //   - Sunday, August 3, 20258:15 PM...8:30 PM...
        // output date in YYYY-MM-DD format and times in HH:mm:ss format

        let date = null;

        if (!text) {
            return {
                date: null,
                times: [],
            };
        }

        const datePatterns = [
            /(January|February|March|April|May|June|July|August|September|October|November|December) (\d+), (\d{4})/,
            /(January|February|March|April|May|June|July|August|September|October|November|December) (\d{1,2})/,
            /(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d+), (\d{4})/,
            /(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{1,2})/,
        ];

        const monthMap = {
            January: 1,
            February: 2,
            March: 3,
            April: 4,
            May: 5,
            June: 6,
            July: 7,
            August: 8,
            September: 9,
            October: 10,
            November: 11,
            December: 12,
            Jan: 1,
            Feb: 2,
            Mar: 3,
            Apr: 4,
            May: 5,
            Jun: 6,
            Jul: 7,
            Aug: 8,
            Sep: 9,
            Oct: 10,
            Nov: 11,
            Dec: 12,
        };

        for (const pattern of datePatterns) {
            const dateMatch = text.match(pattern);
            if (dateMatch) {
                const day = dateMatch[2];
                const year = dateMatch[3] || new Date().getFullYear();

                const monthStr = dateMatch[1];
                const month = monthMap[monthStr];

                date = `${year}-${month.toString().padStart(2, "0")}-${day.toString().padStart(2, "0")}`;
                text = text.replace(dateMatch[0], "");
                break;
            }
        }

        const times = parseTimes(text);

        return {
            date: date,
            times: times,
        };
    };

    const timestampToDateAndTime = (timestamp) => {
        const date = new Date(timestamp);
        return {
            date: date.toISOString().split("T")[0],
            time: date.toISOString().split("T")[1].split(".")[0],
        };
    };

    const getNextDate = (date, deltaDays = 1) => {
        const d = new Date(date);
        d.setDate(d.getDate() + deltaDays);
        return d.toISOString().split("T")[0];
    };

    const getNextTime = (time, deltaMinutes) => {
        const [hours, minutes, seconds] = time.split(":").map(Number);
        let nextMinutes = minutes + deltaMinutes;
        let nextHours = hours + Math.floor(nextMinutes / 60);
        nextMinutes %= 60;
        // We deliberately do not mod nextHours by 24 here to allow for times like "24:00:00"
        return `${nextHours.toString().padStart(2, "0")}:${nextMinutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`;
    };

    const parseTimesAndAvailabilities = (date, timesArray) => {
        // input `date` is in YYYY-MM-DD format
        // input `timesArray` is an array of strings like ["", "", "8:30 PM", "9:00 PM", ""]
        // output is an array of objects with date, time, and availability
        if (timesArray.length === 0 || !timesArray.some(time => time !== "")) {
            return [];
        }

        // infer the delta minutes between times
        const isQuarterHour = timesArray.some(time => time.includes(":15") || time.includes(":45"));
        const deltaMinutes = isQuarterHour ? 15 : 30;
        const deltaMilliseconds = deltaMinutes * 60 * 1000;

        // first canonicalize the date and times into timestamps
        const timestamps = timesArray.map(time => {
            const times = parseTimes(time);
            if (times.length > 0) {
                const timestamp = new Date(date + "T" + times[0] + "Z");  // UTC time
                return timestamp.getTime();
            }
            return null;
        });
        const n = timestamps.length;

        // now we can parse the availabilities
        const availabilities = [];
        let i = 0;
        while (i < n) {
            let j = i;
            while (j < n && timestamps[j] === null) {
                ++j;
            }
            if (i === j) {
                // t[i] is available
                const dt = timestampToDateAndTime(timestamps[i]);
                availabilities.push({
                    date: dt.date,
                    time: dt.time,
                    availability: "available",
                });
                ++i;
            } else if (i > 0 && j < n) {
                // t[i-1] is available, t[j] is available, t[i] ... t[j-1] are unavailable
                // we will fill in between t[i-1] + deltaMinutes, t[i-1] + 2 * deltaMinutes, ... t[i-1] + k * deltaMinutes
                const start = timestamps[i - 1];
                const end = timestamps[j];
                let current = start + deltaMilliseconds;
                while (current < end) {
                    const dt = timestampToDateAndTime(current);
                    availabilities.push({
                        date: dt.date,
                        time: dt.time,
                        availability: "unavailable",
                    });
                    current += deltaMilliseconds;
                }
                i = j;
            } else if (i === 0) {
                // t[0] ... t[j-1] are unavailable
                let current = timestamps[j];
                for (let k = j - 1; k >= i; --k) {
                    current -= deltaMilliseconds;
                    const dt = timestampToDateAndTime(current);
                    availabilities.push({
                        date: dt.date,
                        time: dt.time,
                        availability: "unavailable",
                    });
                }
                i = j;
            } else {
                // t[i] ... t[n-1] are unavailable
                let current = timestamps[i - 1];
                for (let k = i; k < n; ++k) {
                    current += deltaMilliseconds;
                    const dt = timestampToDateAndTime(current);
                    availabilities.push({
                        date: dt.date,
                        time: dt.time,
                        availability: "unavailable",
                    });
                }
                i = j;
            }
        }
        return availabilities;
    };

    const handleSearchPage = () => {
        let partySize = null;
        document.querySelectorAll('[data-testid="party-size-picker-overlay"]').forEach((el) => {
            if (isVisible(el)) {
                partySize = parsePartySize(el.textContent);
            }
        });

        let baseDate = null;
        document.querySelectorAll('[data-testid="day-picker-overlay"]').forEach((el) => {
            if (isVisible(el)) {
                baseDate = el.textContent;
            }
        });

        let baseTime = null;
        document.querySelectorAll('[data-testid="time-picker-overlay"]').forEach((el) => {
            if (isVisible(el)) {
                baseTime = el.textContent;
            }
        });

        if (baseDate && baseTime) {
            const { date, times } = parseDateAndTimes(baseDate + " " + baseTime);
            if (times.length > 0) {
                baseDate = date;
                baseTime = times[0];
            }
        }

        // there could be some promoted restaurants
        document.querySelectorAll('[data-test="multi-search-pop-table"] > ul > li').forEach((el) => {
            if (isVisible(el)) {
                if (isRecorded(el)) {
                    return;
                } else {
                    setIsRecorded(el);
                }
                const restaurantName = (el.querySelector('[data-test="res-card-name"]') || el.querySelector('h6'))?.textContent;
                const timeSlots = el.querySelector('[data-test="time-slots"]')?.textContent;
                if (timeSlots && timeSlots.includes("no online availability")) {
                    results.push({
                        url: url,
                        restaurantName: restaurantName,
                        partySize: partySize,
                        date: baseDate,
                        time: baseTime,
                        info: timeSlots,
                    });
                } else {
                    const timesArray = Array.from(el.querySelectorAll('li'))
                        .map((el) => el.textContent)
                        .filter((el) => el === "" || el.includes("AM") || el.includes("PM"));
                    const availabilities = parseTimesAndAvailabilities(baseDate, timesArray);
                    for (const a of availabilities) {
                        results.push({
                            url: url,
                            restaurantName: restaurantName,
                            partySize: partySize,
                            date: a.date,
                            time: a.time,
                            info: a.availability,
                        });
                    }
                }
            }
        });

        // go through the search result for each restaurant
        document.querySelectorAll('[data-test="restaurant-card"]').forEach((el) => {
            if (isVisible(el)) {
                if (isRecorded(el)) {
                    return;
                }
                const restaurantName = (el.querySelector('[data-test="res-card-name"]') || el.querySelector('h6'))?.textContent;
                const timeSlots = el.querySelector('[data-test="time-slots"]')?.textContent;
                if (timeSlots && timeSlots.includes("no online availability")) {
                    results.push({
                        url: url,
                        restaurantName: restaurantName,
                        partySize: partySize,
                        date: baseDate,
                        time: baseTime,
                        info: timeSlots,
                    });
                    setIsRecorded(el);
                } else {
                    const timesArray = Array.from(el.querySelectorAll('li'))
                        .map((el) => el.textContent)
                        .filter((el) => el === "" || el.includes("AM") || el.includes("PM"));
                    const availabilities = parseTimesAndAvailabilities(baseDate, timesArray);
                    for (const a of availabilities) {
                        results.push({
                            url: url,
                            restaurantName: restaurantName,
                            partySize: partySize,
                            date: a.date,
                            time: a.time,
                            info: a.availability,
                        });
                    }
                    if (availabilities.length > 0) {
                        setIsRecorded(el);
                    }
                }

            }
        });

        // Popup when clicking "Show next available"
        const popup = document.querySelector('[data-test="multi-day-availability-modal"]');
        if (popup) {
            const restaurantName = popup.querySelector('h2')?.textContent;
            let prevAvailable = null;
            let nextAvailable = null;
            popup.querySelectorAll('[data-test="multi-day-timeslot-container"]').forEach((el) => {
                if (isVisible(el)) {
                    const { date, _ } = parseDateAndTimes(el.textContent);
                    const timesArray = Array.from(el.querySelectorAll('li'))
                        .map((el) => el.textContent)
                        .filter((el) => el === "" || el.includes("AM") || el.includes("PM"));
                    const availabilities = parseTimesAndAvailabilities(date, timesArray);

                    for (const a of availabilities) {
                        results.push({
                            url: url,
                            restaurantName: restaurantName,
                            partySize: partySize,
                            date: a.date,
                            time: a.time,
                            info: a.availability,
                        });
                        if (a.availability === "available") {
                            // update prevAvailable
                            if (a.date < baseDate || (a.date === baseDate && a.time <= baseTime)) {
                                if (prevAvailable === null) {
                                    prevAvailable = {
                                        date: a.date,
                                        time: a.time,
                                    };
                                } else if (a.date > prevAvailable.date || (a.date === prevAvailable.date && a.time > prevAvailable.time)) {
                                    prevAvailable = {
                                        date: a.date,
                                        time: a.time,
                                    };
                                }
                            }

                            // update nextAvailable
                            if (a.date > baseDate || (a.date === baseDate && a.time > baseTime)) {
                                if (nextAvailable === null) {
                                    nextAvailable = {
                                        date: a.date,
                                        time: a.time,
                                    };
                                } else if (a.date < nextAvailable.date || (a.date === nextAvailable.date && a.time < nextAvailable.time)) {
                                    nextAvailable = {
                                        date: a.date,
                                        time: a.time,
                                    };
                                }
                            }
                        }
                    }
                }
            });

            // if the first page in the popup, then no available slots between the base query
            // date/time and the first available date/time in the popup
            const curPage = popup.querySelector('.qfZDsxm8aWs-')?.textContent;
            if (curPage === "1") {
                if (prevAvailable && nextAvailable) {
                    results.push({
                        url: url,
                        restaurantName: restaurantName,
                        partySize: partySize,
                        startDate: prevAvailable.date,  // inclusive
                        startTime: prevAvailable.time,  // inclusive
                        endDate: nextAvailable.date,  // exclusive
                        endTime: nextAvailable.time,  // exclusive
                        info: "unavailable",
                    });
                } else if (nextAvailable) {
                    results.push({
                        url: url,
                        restaurantName: restaurantName,
                        partySize: partySize,
                        startDate: baseDate,  // inclusive
                        startTime: baseTime,  // inclusive
                        endDate: nextAvailable.date,  // exclusive
                        endTime: nextAvailable.time,  // exclusive
                        info: "unavailable",
                    });
                }
            }
        }
    };

    const handleBookingPage = () => {
        const restaurantName = document.querySelector('[data-test="restaurantName"]')?.textContent;

        let partySize = null;
        document.querySelectorAll('[data-test="icPerson-wrapper"]').forEach((el) => {
            if (isVisible(el)) {
                partySize = parsePartySize(el.textContent);
            }
        });

        let baseDate = null;
        document.querySelectorAll('[data-test="icCalendar-wrapper"]').forEach((el) => {
            if (isVisible(el)) {
                baseDate = el.textContent;
            }
        });

        let baseTime = null;
        document.querySelectorAll('[data-test="icClock-wrapper"]').forEach((el) => {
            if (isVisible(el)) {
                baseTime = el.textContent;
            }
        });

        if (baseDate && baseTime) {
            const dateAndTimes = parseDateAndTimes(baseDate + " " + baseTime);
            for (const time of dateAndTimes.times) {
                results.push({
                    url: url,
                    restaurantName: restaurantName,
                    partySize: partySize,
                    date: dateAndTimes.date,
                    time: time,
                    info: "available",
                });
            }
        }
    };

    const handleRestrefPage = () => {
        let restaurantName = document.querySelector('h1')?.textContent;
        if (restaurantName.startsWith("Reservation at ")) {
            restaurantName = restaurantName.slice(15);
        }

        let partySize = null;
        document.querySelectorAll('.styled__PartySizeSelectorWrapper-sc-d8dhde-1').forEach((el) => {
            if (isVisible(el)) {
                partySize = parsePartySize(el.textContent);
            }
        });

        let baseDate = null;
        document.querySelectorAll('.styled__DatePickerWrapper-sc-d8dhde-2').forEach((el) => {
            if (isVisible(el)) {
                baseDate = el.textContent;
            }
        });

        let baseTime = null;
        document.querySelectorAll('.styled__TimeSelectorWrapper-sc-d8dhde-3').forEach((el) => {
            if (isVisible(el)) {
                baseTime = el.querySelector('.styled__Label-sc-7ysqo8-0')?.textContent;
            }
        });

        if (baseDate && baseTime) {
            const { date, times } = parseDateAndTimes(baseDate + " " + baseTime);
            if (times.length > 0) {
                baseDate = date;
                baseTime = times[0];
            }
        }

        let availability = null;
        document.querySelectorAll('.styled__AvailabilityDayWrapper-sc-1xhoeow-5').forEach((el) => {
            if (isVisible(el)) {
                availability = el.querySelector('p')?.textContent;
            }
        });

        if (availability) {
            results.push({
                url: url,
                restaurantName: restaurantName,
                partySize: partySize,
                date: baseDate,
                time: baseTime,
                info: availability,
            });
        }

        // searched day slots
        document.querySelectorAll('.styled__AvailabilityDayWrapper-sc-1xhoeow-5').forEach((el) => {
            if (isVisible(el)) {
                const timesArray = Array.from(el.querySelectorAll('li'))
                    .map((el) => el.textContent)
                    .filter((el) => el === "" || el.includes("am") || el.includes("pm"));
                const availabilities = parseTimesAndAvailabilities(baseDate, timesArray);

                for (const a of availabilities) {
                    results.push({
                        url: url,
                        restaurantName: restaurantName,
                        partySize: partySize,
                        date: a.date,
                        time: a.time,
                        info: a.availability,
                    });
                }
            }
        });

        // future availability
        document.querySelectorAll('.styled__StyledFutureAvailabilityDayWrapper-sc-2dwu07-1').forEach((el) => {
            if (isVisible(el)) {
                const dateText = el.querySelector('p')?.textContent;
                const { date, _ } = parseDateAndTimes(dateText);
                const timesArray = Array.from(el.querySelectorAll('li'))
                    .map((el) => el.textContent)
                    .filter((el) => el === "" || el.includes("am") || el.includes("pm"));
                console.log(`date: ${date}, timesArray: ${timesArray}`);
                const availabilities = parseTimesAndAvailabilities(date, timesArray);

                for (const a of availabilities) {
                    results.push({
                        url: url,
                        restaurantName: restaurantName,
                        partySize: partySize,
                        date: a.date,
                        time: a.time,
                        info: a.availability,
                    });
                }
            }
        });
    };

    const handleBookingRestrefPage = () => {
        let restaurantName = document.querySelector('h1')?.textContent;
        if (restaurantName.startsWith("Booking at ")) {
            restaurantName = restaurantName.slice(11);
        }

        let partySize = null;
        document.querySelectorAll('[data-testid="party-size-picker-overlay"]').forEach((el) => {
            if (isVisible(el)) {
                partySize = parsePartySize(el.textContent);
            }
        });

        let baseDate = null;
        document.querySelectorAll('[data-testid="day-picker-overlay"]').forEach((el) => {
            if (isVisible(el)) {
                baseDate = el.textContent;
            }
        });

        let baseTime = null;
        document.querySelectorAll('[data-testid="time-picker-overlay"]').forEach((el) => {
            if (isVisible(el)) {
                baseTime = el.textContent;
            }
        });

        if (baseDate && baseTime) {
            const { date, times } = parseDateAndTimes(baseDate + " " + baseTime);
            if (times.length > 0) {
                baseDate = date;
                baseTime = times[0];
            }
        }

        let availability = null;
        document.querySelectorAll('.O-z6wyHTamU-').forEach((el) => {
            if (isVisible(el)) {
                availability = el.textContent;
            }
        });

        if (availability) {
            results.push({
                url: url,
                restaurantName: restaurantName,
                partySize: partySize,
                date: baseDate,
                time: baseTime,
                info: availability,
            });
        }

        // searched day slots
        document.querySelectorAll('[data-test="searched-day-slots"]').forEach((el) => {
            if (isVisible(el)) {
                const timesArray = Array.from(el.querySelectorAll('[data-test="slot"]'))
                    .map((el) => el.textContent)
                    .filter((el) => el === "" || el.includes("AM") || el.includes("PM"));
                const availabilities = parseTimesAndAvailabilities(baseDate, timesArray);

                for (const a of availabilities) {
                    results.push({
                        url: url,
                        restaurantName: restaurantName,
                        partySize: partySize,
                        date: a.date,
                        time: a.time,
                        info: a.availability,
                    });
                }
            }
        });

        // future availability
        document.querySelectorAll('[data-test="future-availability-row"]').forEach((el) => {
            if (isVisible(el)) {
                const dateText = el.querySelector('p')?.textContent;
                const { date, _ } = parseDateAndTimes(dateText);
                const timesArray = Array.from(el.querySelectorAll('[data-test="slot"]'))
                    .map((el) => el.textContent)
                    .filter((el) => el === "" || el.includes("AM") || el.includes("PM"));
                const availabilities = parseTimesAndAvailabilities(date, timesArray);

                for (const a of availabilities) {
                    results.push({
                        url: url,
                        restaurantName: restaurantName,
                        partySize: partySize,
                        date: a.date,
                        time: a.time,
                        info: a.availability,
                    });
                }
            }
        });
    };

    const handleRestaurantPageWithFullAvailabilityPopup = () => {
        const popup = document.querySelector('[data-testid="multi-day-availability-modal"]');
        if (!popup) return;

        const restaurantName = popup.querySelector('h2')?.textContent;

        let partySize = null;
        popup.querySelectorAll('[data-testid="party-size-picker-overlay"]').forEach((el) => {
            if (isVisible(el)) {
                partySize = parsePartySize(el.textContent);
            }
        });

        let baseDate = null;
        popup.querySelectorAll('.sEh3MIECg10-').forEach((el) => {
            if (isVisible(el)) {
                baseDate = parseDateAndTimes(el.textContent).date;
            }
        });

        let timeSlots = null;
        const elements = popup.querySelectorAll('[data-test="time-slots"]');
        for (const el of elements) {
            if (isVisible(el)) {
                timeSlots = el;
                break;
            }
        }

        if (timeSlots && timeSlots.textContent.includes("no online availability on the selected day")) {
            results.push({
                url: url,
                restaurantName: restaurantName,
                partySize: partySize,
                startDate: baseDate,  // inclusive
                startTime: "00:00:00",  // inclusive
                endDate: getNextDate(baseDate),  // exclusive
                endTime: "00:00:00",  // exclusive
                info: "unavailable",
            });
        } else if (timeSlots && timeSlots.textContent.includes("Unfortunately")) {
            results.push({
                url: url,
                restaurantName: restaurantName,
                partySize: partySize,
                date: baseDate,
                time: "00:00:00",  // here the time is not actually used, as the entire date or the party size is not available
                info: timeSlots.textContent,
            });
        } else if (timeSlots) {
            const timesAndVisibilities = [];

            const slots = timeSlots.querySelectorAll('li');
            for (const slot of slots) {
                const results = parseTimes(slot.textContent);
                if (results.length === 1) {
                    timesAndVisibilities.push({ time: results[0], visibility: isVisible(slot) });
                }
            }

            if (timesAndVisibilities.length === 0) {
                return;
            }

            timesAndVisibilities.sort((a, b) => a.time === b.time ? 0 : a.time < b.time ? -1 : 1);

            // Algorithm to infer the availability info the agent sees
            // Example inputs:
            //   - baseDate: "2025-12-11"
            //   - timesAndVisibilities: [
            //       {"time": "11:30:00", "visibility": false},
            //       {"time": "11:45:00", "visibility": true},
            //       {"time": "14:30:00", "visibility": true},
            //       {"time": "20:00:00", "visibility": true},
            //       {"time": "20:15:00", "visibility": false}
            //     ]
            // Process:
            //   1. Determine the delta minutes between times (15 minutes or 30 minutes)
            //   2. Get the earliest and latest available times, denoted as L and R, respectively
            //   3. Get the earliest and latest visible times, denoted as a and b, respectively
            //   4. There are four cases:
            //      a) L == a and b == R: then all the info is accessible by the agent
            //      b) L == a and b < R: then all the info before b is accessible by the agent
            //      c) L < a and b == R: then all the info after a is accessible by the agent
            //      d) L < a and b < R: then the info between a and b is accessible by the agent

            const deltaMinutes = timesAndVisibilities.some(t => t.time.includes(":15:")) ? 15 : 30;

            const L = timesAndVisibilities[0].time;
            const R = timesAndVisibilities[timesAndVisibilities.length - 1].time;
            const a = timesAndVisibilities.find(t => t.visibility)?.time;
            const b = timesAndVisibilities.findLast(t => t.visibility)?.time;

            let start = "00:00:00";
            let end = "23:59:59";
            if (L === a && b === R) {
                // pass
            } else if (L === a && b < R) {
                end = b;
            } else if (L < a && b === R) {
                start = a;
            } else if (L < a && b < R) {
                start = a;
                end = b;
            }

            const availableTimes = new Set(timesAndVisibilities.map(x => x.time));
            for (let t = start; t <= end; t = getNextTime(t, deltaMinutes)) {
                results.push({
                    url: url,
                    restaurantName: restaurantName,
                    partySize: partySize,
                    date: baseDate,
                    time: t,
                    info: availableTimes.has(t) ? "available" : "unavailable",
                });
            }
        }
    };

    const handleRestaurantPage = () => {
        if (document.querySelectorAll('[data-testid="multi-day-availability-modal"]').length > 0) {
            return handleRestaurantPageWithFullAvailabilityPopup();
        }

        const restaurantName = document.querySelector('h1')?.textContent;

        // Locate the side panel for reservation of the restaurant
        let reservationPanel = null;
        document.querySelectorAll('[data-testid="bookable-cta"]').forEach((el) => {
            if (isVisible(el)) {
                reservationPanel = el;
            }
        });
        if (!reservationPanel) return;

        let partySize = null;
        let baseDate = null;
        let baseTime = null;

        const editButton = reservationPanel.querySelector('[data-testid="icEdit"]') ? reservationPanel.querySelector('button') : null;
        if (editButton && isVisible(editButton)) {
            // Dropdown menus for party size, date, and time are not shown directly.
            // Instead, there is a span of text like "For 2 people, Dec 17, 2025, 7:00 PM", together with an edit button.
            // Agents need to click the edit button to show the dropdown menus.
            // Here we parse the text span to get the party size, date, and time.

            const infoText = editButton.textContent;
            partySize = parsePartySize(infoText);
            const { date, times } = parseDateAndTimes(infoText);
            if (times.length > 0) {
                baseDate = date;
                baseTime = times[0];
            }

        } else {
            // Dropdown menus for party size, date, and time are shown directly
            reservationPanel.querySelectorAll('[data-testid="party-size-picker-overlay"]').forEach((el) => {
                if (isVisible(el)) {
                    partySize = parsePartySize(el.textContent);
                }
            });

            reservationPanel.querySelectorAll('[data-testid="day-picker-overlay"]').forEach((el) => {
                if (isVisible(el)) {
                    baseDate = el.textContent;
                }
            });

            reservationPanel.querySelectorAll('[data-testid="time-picker-overlay"]').forEach((el) => {
                if (isVisible(el)) {
                    baseTime = el.textContent;
                }
            });

            if (baseDate && baseTime) {
                const { date, times } = parseDateAndTimes(baseDate + " " + baseTime);
                if (times.length > 0) {
                    baseDate = date;
                    baseTime = times[0];
                }
            }
        }

        let timeSlots = null;
        const elements = reservationPanel.querySelectorAll('[data-test="time-slots"]');
        for (const el of elements) {
            if (isVisible(el)) {
                timeSlots = el.textContent;
                break;
            }
        }

        if (timeSlots && (timeSlots.includes("no online availability") || timeSlots.includes("Unfortunately"))) {
            results.push({
                url: url,
                restaurantName: restaurantName,
                partySize: partySize,
                date: baseDate,
                time: baseTime,
                info: timeSlots,
            });
        } else {
            const dateAndTimes = parseDateAndTimes(timeSlots);

            const availableTimes = dateAndTimes.times;
            for (const time of availableTimes) {
                results.push({
                    url: url,
                    restaurantName: restaurantName,
                    partySize: partySize,
                    date: baseDate,
                    time: time,
                    info: "available",
                });
            }

            if (availableTimes.length > 0) {
                // infer the delta minutes between times
                const isQuarterHour = availableTimes.some(time => time.includes(":15:") || time.includes(":45:"));
                const deltaMinutes = isQuarterHour ? 15 : 30;
                const deltaMilliseconds = deltaMinutes * 60 * 1000;

                // convert the available times from string to timestamps
                const availableTimestamps = availableTimes.map(time => new Date(`${baseDate}T${time}Z`).getTime());
                const minAvailableTimestamp = Math.min(...availableTimestamps);
                const maxAvailableTimestamp = Math.max(...availableTimestamps);

                let unavailableTimestamps = new Set();

                // first, any min + k * deltaMinutes that is not in availableTimes is unavailable
                let currentTimestamp = minAvailableTimestamp + deltaMilliseconds;
                while (currentTimestamp < maxAvailableTimestamp) {
                    if (!availableTimestamps.includes(currentTimestamp)) {
                        unavailableTimestamps.add(currentTimestamp);
                    }
                    currentTimestamp += deltaMilliseconds;
                }

                const baseTimestamp = new Date(`${baseDate}T${baseTime}Z`).getTime();
                if (baseTimestamp < minAvailableTimestamp) {
                    // if baseTimestamp < minAvailableTimestamp, then any time from the set {baseTimestamp, ..., minAvailableTimestamp - deltaMilliseconds} is unavailable
                    for (let i = baseTimestamp; i < minAvailableTimestamp; i += deltaMilliseconds) {
                        unavailableTimestamps.add(i);
                    }
                } else if (baseTimestamp > maxAvailableTimestamp) {
                    // if baseTimestamp > maxAvailableTimestamp, then any time from the set {maxAvailableTimestamp + deltaMilliseconds, ..., baseTimestamp} is unavailable
                    for (let i = maxAvailableTimestamp + deltaMilliseconds; i <= baseTimestamp; i += deltaMilliseconds) {
                        unavailableTimestamps.add(i);
                    }
                }

                // any time from the set {baseTime - 2 * deltaMinutes, ..., baseTime + 2 * deltaMinutes} that is not in availableTimes is unavailable
                for (let i = baseTimestamp - 2 * deltaMilliseconds; i <= baseTimestamp + 2 * deltaMilliseconds; i += deltaMilliseconds) {
                    if (!availableTimestamps.includes(i)) {
                        unavailableTimestamps.add(i);
                    }
                }

                // push the unavailable times to the results
                for (const ts of unavailableTimestamps) {
                    const dt = timestampToDateAndTime(ts);
                    results.push({
                        url: url,
                        restaurantName: restaurantName,
                        partySize: partySize,
                        date: dt.date,
                        time: dt.time,
                        info: "unavailable",
                    });
                }
            }
        }

        // Popup when clicking "Show next available"
        const popup = document.querySelector('[data-test="multi-day-availability-modal"]');
        if (popup) {
            const restaurantName = popup.querySelector('h2')?.textContent;
            let prevAvailable = null;
            let nextAvailable = null;
            popup.querySelectorAll('[data-test="multi-day-timeslot-container"]').forEach((el) => {
                if (isVisible(el)) {
                    const { date, _ } = parseDateAndTimes(el.textContent);
                    const timesArray = Array.from(el.querySelectorAll('li'))
                        .map((el) => el.textContent)
                        .filter((el) => el === "" || el.includes("AM") || el.includes("PM"));
                    const availabilities = parseTimesAndAvailabilities(date, timesArray);

                    for (const a of availabilities) {
                        results.push({
                            url: url,
                            restaurantName: restaurantName,
                            partySize: partySize,
                            date: a.date,
                            time: a.time,
                            info: a.availability,
                        });
                        if (a.availability === "available") {
                            // update prevAvailable
                            if (a.date < baseDate || (a.date === baseDate && a.time <= baseTime)) {
                                if (prevAvailable === null) {
                                    prevAvailable = {
                                        date: a.date,
                                        time: a.time,
                                    };
                                } else if (a.date > prevAvailable.date || (a.date === prevAvailable.date && a.time > prevAvailable.time)) {
                                    prevAvailable = {
                                        date: a.date,
                                        time: a.time,
                                    };
                                }
                            }

                            // update nextAvailable
                            if (a.date > baseDate || (a.date === baseDate && a.time > baseTime)) {
                                if (nextAvailable === null) {
                                    nextAvailable = {
                                        date: a.date,
                                        time: a.time,
                                    };
                                } else if (a.date < nextAvailable.date || (a.date === nextAvailable.date && a.time < nextAvailable.time)) {
                                    nextAvailable = {
                                        date: a.date,
                                        time: a.time,
                                    };
                                }
                            }
                        }
                    }
                }
            });

            // if the first page in the popup, then no available slots between the base query
            // date/time and the first available date/time in the popup
            const curPage = popup.querySelector('.qfZDsxm8aWs-')?.textContent;
            if (curPage === "1") {
                if (prevAvailable && nextAvailable) {
                    results.push({
                        url: url,
                        restaurantName: restaurantName,
                        partySize: partySize,
                        startDate: prevAvailable.date,  // inclusive
                        startTime: prevAvailable.time,  // inclusive
                        endDate: nextAvailable.date,  // exclusive
                        endTime: nextAvailable.time,  // exclusive
                        info: "unavailable",
                    });
                } else if (nextAvailable) {
                    results.push({
                        url: url,
                        restaurantName: restaurantName,
                        partySize: partySize,
                        startDate: baseDate,  // inclusive
                        startTime: baseTime,  // inclusive
                        endDate: nextAvailable.date,  // exclusive
                        endTime: nextAvailable.time,  // exclusive
                        info: "unavailable",
                    });
                }
            }
        }
    };

    if (url.includes("opentable.com/s?") || /opentable\.com\/[^/]+-restaurant-listings/.test(url)) {
        // search page
        handleSearchPage();
    } else if (url.includes("opentable.com/restref")) {
        // restref page
        handleRestrefPage();
    } else if (url.includes("opentable.com/booking/restref")) {
        // booking restref page
        handleBookingRestrefPage();
    } else if (url.includes("opentable.com/booking/")) {
        // booking page
        handleBookingPage();
    } else if (url.includes("opentable.com/")) {
        // restaurant page
        handleRestaurantPage();
    }

    return results;
})();
