package io.jmetervsghz.sut.metrics;

import io.grpc.Attributes;
import io.grpc.ServerTransportFilter;
import io.micrometer.core.instrument.MeterRegistry;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import net.devh.boot.grpc.server.serverfactory.GrpcServerConfigurer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Counts transport-level connections, which is the evidence behind the central
 * architectural claim of this benchmark.
 *
 * <p>A gauge of "distinct peer addresses ever seen" would be wrong here: it only
 * grows, so it would report the sum of every run since the server started rather
 * than what one tool did. This tracks connections as they open and close, so
 * {@code benchmark_server_connections_active} is the number of TCP connections
 * the load generator is holding *right now*.
 *
 * <p>Read against concurrency, this separates the two tools structurally: the
 * JMeter plugin opens one channel per thread, so active connections track thread
 * count, while ghz multiplexes its workers over whatever {@code --connections}
 * it was given.
 */
@Configuration
public class ConnectionTracker {

  private final AtomicInteger active = new AtomicInteger();
  private final AtomicLong opened = new AtomicLong();

  public ConnectionTracker(MeterRegistry registry) {
    registry.gauge("benchmark_server_connections_active", active);
    registry.gauge("benchmark_server_connections_opened", opened);
  }

  @Bean
  public GrpcServerConfigurer connectionTrackingConfigurer() {
    return serverBuilder ->
        serverBuilder.addTransportFilter(
            new ServerTransportFilter() {
              @Override
              public Attributes transportReady(Attributes transportAttrs) {
                active.incrementAndGet();
                opened.incrementAndGet();
                return transportAttrs;
              }

              @Override
              public void transportTerminated(Attributes transportAttrs) {
                active.decrementAndGet();
              }
            });
  }
}
